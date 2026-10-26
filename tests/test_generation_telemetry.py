"""Phase 5 attempts/lifecycle regression tests; isolated SQL, HTTP mocks and fake B2."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.auth import get_current_api_key
from app.models.db_models import MediaGeneration, MediaModel, TTSJob
from app.services import generation_telemetry as telemetry
from app.services import media_gen_service
from app.services.request_history import history_detail, history_page, history_summary
from app.services.tts import gemini_provider, jobs
from fastapi import HTTPException, Request
from main import app
from sqlalchemy import select

from tests.test_tts_chunks import context, run, seed  # noqa: F401


def request(user="customer-7"):
    headers = [] if user is None else [(b"x-external-user-id", user.encode())]
    return Request({"type": "http", "headers": headers, "state": {"request_id": "request-1"}})


async def test_attempt_lifecycle_no_key_cancel_and_unknown_cost():
    events = []

    async def sink(event):
        events.append(dict(event))

    with telemetry.telemetry_scope(sink, model="test", provider="gemini", operation_id="chunk:0"):
        # Eligibility failure before sending anything is not a provider call.
        with pytest.raises(ValueError):
            async with telemetry.provider_attempt("tts"):
                raise ValueError("No eligible key; secret must not be persisted")
        assert events == []
        with pytest.raises(asyncio.CancelledError):
            async with telemetry.provider_attempt("tts") as measured:
                await telemetry.begin_attempt()
                assert events[-1]["status"] == "processing"
                measured["prompt_tokens"] = 3
                raise asyncio.CancelledError("DO-NOT-EXPOSE")
    assert [e["status"] for e in events] == ["processing", "cancelled"]
    assert events[0]["attempt_id"] == events[1]["attempt_id"]
    assert events[-1]["prompt_tokens"] == 3 and events[-1]["cost_usd"] is None
    assert events[-1]["elapsed_ms"] >= 0 and events[-1]["finished_at"]
    assert "DO-NOT-EXPOSE" not in json.dumps(events)
    assert telemetry.attempt_scope.get() is telemetry.active_attempt.get() is None


def test_external_user_is_an_app_reference_not_a_gateway_owner():
    assert telemetry.request_identity(request(), 3)["authorized_app_id"] == 3
    assert telemetry.request_identity(request(), 9)["external_user_id"] == "customer-7"
    assert telemetry.request_identity(request(None), 3)["external_user_id"] is None
    for value in ("", "x" * 129, "bad\nreference", "bad\x7freference"):
        with pytest.raises(HTTPException) as error:
            telemetry.request_identity(request(value), 3)
        assert error.value.status_code == 422


@pytest.mark.parametrize("automatic_retry", [False, True])
async def test_tts_failed_chunk_resume_retains_actual_attempts(context, monkeypatch, automatic_retry):  # noqa: F811
    db, _, _, _ = context
    job = await seed(db, count=2)
    data = json.loads(job.generation_json)
    data["_telemetry"] = {"version": 1, "coverage_complete": True, "attempts": [], "external_user_id": "customer-7"}
    job.generation_json = json.dumps(data)
    await db.commit()
    monkeypatch.setattr(gemini_provider, "_resolve_key", AsyncMock(return_value=gemini_provider._ActiveKey("DO-NOT-EXPOSE")))
    monkeypatch.setattr(gemini_provider, "_pace_request", AsyncMock())
    monkeypatch.setattr(gemini_provider, "_mark_used", AsyncMock())
    monkeypatch.setattr(gemini_provider, "GEMINI_TTS_MAX_RETRIES", int(automatic_retry))
    monkeypatch.setattr(gemini_provider, "_backoff", lambda attempt: 0)
    recover = False
    seen = 0

    def respond(req):
        nonlocal seen
        text = json.loads(req.content)["contents"][0]["parts"][0]["text"]
        if text == "1":
            seen += 1
        if text == "1" and not recover and (not automatic_retry or seen == 1):
            return httpx.Response(503, json={"usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
                                             "error": "DO-NOT-EXPOSE"})
        return httpx.Response(200, json={"usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 8},
            "candidates": [{"content": {"parts": [{"inlineData": {
                "data": base64.b64encode(b"\0\0" * 240).decode(), "mimeType": "audio/pcm;rate=24000"}}]}}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(gemini_provider.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    await run(job)
    failed = await history_detail(db, "tts:job")
    if automatic_retry:
        assert failed["status"] == "completed" and len(failed["attempts"]) == 3
        assert sum(a["status"] == "failed" for a in failed["attempts"]) == 1
        assert failed["attempts_complete"] is True and failed["provider_cost_complete"] is False
        return
    assert failed["status"] == "failed" and failed["customer_chargeable_tokens"] == 0
    assert failed["duration_ms"] >= 0 and failed["external_user_id"] == "customer-7"
    assert len(failed["attempts"]) == 2 and failed["attempts_complete"] is True
    assert failed["known_provider_cost_usd"] is None and failed["provider_cost_complete"] is False
    consumed = next(a for a in failed["attempts"] if a["status"] == "failed")
    assert consumed["prompt_tokens"] == 2 and consumed["completion_tokens"] == 1
    assert consumed["status_code"] == 503
    recover = True
    token = await jobs.claim_retry(db, "job", 1)
    await run(job, token)
    result = await history_detail(db, "tts:job")
    assert result["status"] == "completed" and result["customer_chargeable_tokens"] is None
    assert len(result["attempts"]) == 3 and result["attempts_complete"] is True
    assert [a["operation_id"] for a in result["attempts"]].count("chunk:1") == 2
    assert next(a for a in result["attempts"] if a["attempt_id"] == consumed["attempt_id"])["status"] == "failed"
    assert "DO-NOT-EXPOSE" not in json.dumps(result, default=str)
    with pytest.raises(RuntimeError, match="no longer owned"):
        await telemetry.persist_attempt(lambda: db, TTSJob, [TTSJob.job_id == "job", TTSJob.run_token == "first"],
                                        "generation_json", consumed)


async def test_media_parallel_failure_snapshot_and_polling_are_not_double_counted(unit_db, monkeypatch):
    db = unit_db
    model = MediaModel(name="original-image", provider="replicate", media_type="image", max_outputs=2,
                       api_base_url="https://provider.test", api_endpoint_path="/create", auth_env_var="FAKE_MEDIA_KEY",
                       is_async_poll=True, poll_endpoint_path="/poll/{id}", poll_status_field="status",
                       poll_success_value="succeeded", poll_failed_value="failed", poll_interval_s=0,
                       response_extraction_config_json=json.dumps({"path": "output[*]"}))
    db.add(model)
    await db.flush()
    audit = MediaGeneration(model_id=model.id, user_id=None, prompt="do not expose",
                            extra_params_json=telemetry.media_metadata(request(), 3, model, {"_telemetry": "untrusted"}))
    db.add(audit)
    await db.commit()
    monkeypatch.setattr("app.db.database.AsyncSessionLocal", lambda: db)
    monkeypatch.setenv("FAKE_MEDIA_KEY", "DO-NOT-EXPOSE")
    submitted = 0

    async def respond(req):
        nonlocal submitted
        if req.method == "POST":
            submitted += 1
            return httpx.Response(201, json={"id": str(submitted)})
        if req.url.path == "/poll/1":
            return httpx.Response(200, json={"status": "failed", "error": "DO-NOT-EXPOSE"})
        await asyncio.sleep(.01)
        return httpx.Response(200, json={"status": "succeeded", "output": ["https://private.test/file"]})

    client = httpx.AsyncClient
    monkeypatch.setattr(media_gen_service.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    with telemetry.media_telemetry(audit.id), pytest.raises(RuntimeError):
        await media_gen_service.generate_media(db, model.name, "test", num_outputs=2, model_snapshot=model)
    await db.refresh(audit)
    audit.status = "failed"
    # Editing the catalog later must not rewrite recorded execution identity.
    model.name, model.provider = "edited-name", "edited-provider"
    await db.commit()
    detail = await history_detail(db, f"media:{audit.id}")
    assert detail["model"] == "original-image" and detail["provider"] == "replicate"
    assert detail["model_provenance"] == "execution_snapshot"
    assert detail["authorized_app_id"] == 3 and detail["external_user_id"] == "customer-7"
    assert detail["customer_chargeable_tokens"] == 0 and detail["attempts_complete"] is True
    assert len(detail["attempts"]) == submitted == 2
    assert {a["status"] for a in detail["attempts"]} == {"completed", "failed"}
    assert len({a["operation_id"] for a in detail["attempts"]}) == 2
    assert detail["known_provider_cost_usd"] is None and "DO-NOT-EXPOSE" not in json.dumps(detail, default=str)
    listing = await history_page(db, {"provider": "replicate"})
    summary = await history_summary(db, {"provider": "replicate"})
    assert listing["total"] == summary["requests"] == 1


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("status", [200, 503, None])
async def test_media_routes_persist_attribution_usage_and_safe_failures(unit_db, monkeypatch, kind, status):
    model = MediaModel(name="route-model", provider="generic_http", media_type=kind, is_async_poll=False,
                       api_base_url="https://provider.test", api_endpoint_path="/create",
                       response_extraction_config_json=json.dumps({"path": "output[*]"}))
    unit_db.add(model)
    await unit_db.commit()

    async def auth(req: Request):
        req.state.authorized_app = SimpleNamespace(id=3, owner_user_id=None)
        return SimpleNamespace(id=1, authorized_app_id=3)

    app.dependency_overrides[get_current_api_key] = auth
    monkeypatch.setattr("app.db.database.AsyncSessionLocal", lambda: unit_db)
    monkeypatch.setattr("app.services.query_telemetry.litellm.completion_cost", lambda **kw: .125)
    if status is None:
        def unavailable(model):
            raise ValueError("No eligible key")
        monkeypatch.setattr(media_gen_service, "_build_auth_headers", unavailable)

    def provider(req):
        assert status is not None  # A key-selection failure must not send a provider request.
        return httpx.Response(status, json={"output": ["https://files.test/output"], "error": "DO-NOT-EXPOSE",
                                           "usage": {"prompt_tokens": 6, "completion_tokens": 2}})

    client = httpx.AsyncClient
    monkeypatch.setattr(media_gen_service.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(provider), **kw))
    async with client(transport=httpx.ASGITransport(app=app), base_url="http://test") as browser:
        response = await browser.post(f"/api/v1/{kind}/generate", headers={"X-External-User-ID": "customer-7"},
                                      json={"model": model.name, "prompt": "test", "extra_params": {"test": True}})
    assert response.status_code == (200 if status == 200 else 500)
    row = (await unit_db.execute(select(MediaGeneration))).scalar_one()
    detail = await history_detail(unit_db, f"media:{row.id}")
    assert detail["status"] == ("completed" if status == 200 else "failed")
    assert detail["authorized_app_id"] == 3 and detail["external_user_id"] == "customer-7"
    assert detail["duration_ms"] >= 0 and detail["attempts_complete"] is True
    assert detail["known_provider_cost_usd"] == (0 if status is None else .125)
    assert detail["provider_cost_complete"] is True
    assert len(detail["attempts"]) == (0 if status is None else 1)
    if status is not None:
        assert detail["attempts"][0]["prompt_tokens"] == 6
    listing = await history_page(unit_db, {})
    assert listing["items"][0]["known_provider_cost_usd"] == detail["known_provider_cost_usd"]
    assert detail["customer_chargeable_tokens"] == (None if status == 200 else 0)
    assert json.loads(row.extra_params_json)["parameters"] == {"test": True}
    assert "DO-NOT-EXPOSE" not in response.text + json.dumps(detail, default=str) + (row.error_message or "")
