# ruff: noqa: F811
"""Generation shares query storage/history, with isolated SQL and fake providers."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api.v1 import generate
from app.core.config import settings
from app.models.db_models import (
    AIModel,
    AuthorizedApp,
    FinalResponse,
    ModelWebsiteUsage,
    Query,
    SearchResult,
    User,
)
from app.services import query_maintenance
from app.services.api_key_service import generate_api_key
from app.services.rule_engine import _check_cache, _md5
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

from tests.test_dashboard import dashboard  # noqa: F401
from tests.test_generation import result, setup_generation  # noqa: F401

GRANTS = [
    "POST /api/v1/generate/",
    "GET /api/v1/query/requests",
    "GET /api/v1/query/requests/{query_id}",
]


@pytest.fixture
async def generation_records(setup_generation, monkeypatch):
    client, headers, provider, db, project = setup_generation
    project.allowed_endpoints_json = json.dumps(GRANTS)
    second_owner = User(
        google_id="second-owner",
        email="second@example.test",
        is_active=True,
        api_access_enabled=True,
    )
    db.add(second_owner)
    await db.flush()
    second = AuthorizedApp(
        name="second-user-project",
        owner_user_id=second_owner.id,
        allowed_origin="",
        is_active=True,
        is_approved=True,
        allowed_endpoints_json=json.dumps(GRANTS),
    )
    db.add(second)
    await db.commit()
    raw, _ = await generate_api_key(db, "second-user", second.id)
    response = result("Generated letter\nСпасибо. café")
    response.model = "actual-model-snapshot"
    response.usage = SimpleNamespace(prompt_tokens=30, completion_tokens=20)
    provider.return_value = response
    monkeypatch.setattr(
        "app.services.query_telemetry.litellm.completion_cost", lambda **kwargs: 0.015
    )
    return client, headers, {"X-API-Key": raw}, provider, db, project


async def test_prompt_response_usage_and_metadata_use_existing_tables(generation_records):
    client, auth, _, call, db, project = generation_records
    prompt = "  Write a letter\nusing these facts. Привет.  "
    response = await client.post(
        "/api/v1/generate/", headers={**auth, "X-Request-ID": "shared-123"}, json={"prompt": prompt}
    )
    assert response.status_code == 200, response.text
    row = db.session.scalars(select(Query)).one()
    assert row.raw_query == prompt and row.authorized_app_id == project.id
    assert row.meta["request_type"] == "generate" and row.meta["endpoint"] == "/api/v1/generate/"
    assert row.meta["request_id"] == "shared-123" and row.meta["api_key_id"] is not None
    assert row.meta["http_status"] == 200 and row.meta["duration_ms"] >= 0
    final = db.session.scalars(select(FinalResponse)).one()
    assert final.query_id == row.id and final.generated_output == response.json()["answer"]
    assert (final.prompt_tokens, final.completion_tokens, final.cost_usd) == (30, 20, 0.015)
    assert final.model_used == "actual-model-snapshot" and final.meta["finish_reason"] == "stop"
    assert final.meta["model_attempts"][0]["status"] == "completed"
    assert row.meta["provider_calls"][0]["model"] == call.await_args.kwargs["model"]
    assert row.meta["total_provider_cost_usd"] == 0.015
    assert db.session.scalars(select(SearchResult)).all() == []
    detail = await client.get(f"/api/v1/query/requests/{row.id}", headers=auth)
    assert detail.status_code == 200 and detail.json()["sources"] == []
    assert detail.json()["answer"] == response.json()["answer"]
    assert detail.headers["cache-control"] == "no-store"
    assert "fake-secret" not in detail.text


async def test_users_cannot_read_each_others_records_or_unassigned_rows(generation_records):
    client, first, second, _, db, _ = generation_records
    for headers, prompt in [(first, "User A private"), (second, "User B private")]:
        assert (
            await client.post("/api/v1/generate/", headers=headers, json={"prompt": prompt})
        ).status_code == 200
    legacy = Query(raw_query="unassigned private")
    db.add(legacy)
    await db.commit()
    for own, other, text, other_text in [
        (first, second, "User A private", "User B private"),
        (second, first, "User B private", "User A private"),
    ]:
        listing = await client.get(
            "/api/v1/query/requests?authorized_app_id=999&owner_user_id=999", headers=own
        )
        assert listing.status_code == 200 and listing.json()["total"] == 1
        record = listing.json()["items"][0]
        assert record["input"] == text and other_text not in listing.text
        assert (
            await client.get(f"/api/v1/query/requests/{record['query_id']}", headers=other)
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/query/requests/{legacy.id}", headers=own)
        ).status_code == 404
    assert (await client.get("/api/v1/query/requests")).status_code == 401


async def test_admin_sees_all_generation_records_but_viewer_cannot(generation_records, dashboard):
    client, first, second, _, _, _ = generation_records
    db, membership, admin = dashboard
    for headers in (first, second):
        assert (
            await client.post(
                "/api/v1/generate/", headers=headers, json={"prompt": "private letter"}
            )
        ).status_code == 200
    response = await client.get("/api/v1/dashboard/query-requests", headers=admin)
    assert response.status_code == 200 and response.json()["total"] == 2
    for item in response.json()["items"]:
        path = f"/api/v1/dashboard/query-requests/{item['query_id']}"
        assert (await client.get(path, headers=admin)).status_code == 200
        assert (await client.get(path, headers=first)).status_code == 401
    membership.role = "viewer"
    await db.commit()
    assert (await client.get("/api/v1/dashboard/query-requests", headers=admin)).status_code == 403
    for item in response.json()["items"]:
        assert (
            await client.get(f"/api/v1/dashboard/query-requests/{item['query_id']}", headers=admin)
        ).status_code == 403


@pytest.mark.parametrize(
    "output,expected_status,error_code,partial",
    [
        (result("partial letter", "length"), 502, "output_truncated", "partial letter"),
        (result("", "content_filter"), 422, "provider_refusal", ""),
        (result(""), 502, "empty_provider_result", ""),
        (SimpleNamespace(choices=[]), 502, "invalid_provider_result", ""),
    ],
)
async def test_failed_response_records_keep_usage_and_never_charge(
    generation_records, output, expected_status, error_code, partial
):
    client, auth, _, provider, db, _ = generation_records
    output.usage = SimpleNamespace(prompt_tokens=30, completion_tokens=20)
    provider.return_value = output
    response = await client.post(
        "/api/v1/generate/", headers=auth, json={"prompt": "private facts"}
    )
    assert response.status_code == expected_status
    row = db.session.scalars(select(Query)).one()
    final = db.session.scalars(select(FinalResponse)).one()
    assert row.meta["status"] == final.meta["status"] == "failed"
    assert row.meta["http_status"] == expected_status and final.meta["error_code"] == error_code
    assert final.generated_output == partial and final.cost_usd == 0.015
    assert row.meta["provider_calls"][0]["status"] == "failed"
    await query_maintenance.maintain_queries(db)
    assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


@pytest.mark.parametrize("kind", ["provider", "overall", "cancelled"])
async def test_deadlines_and_cancellation_finalize_records(generation_records, monkeypatch, kind):
    client, auth, _, provider, db, _ = generation_records

    async def slow(**kwargs):
        if kind == "cancelled":
            raise asyncio.CancelledError()
        await asyncio.sleep(10)

    provider.side_effect = slow
    if kind == "provider":
        monkeypatch.setattr(settings, "generation_provider_timeout_seconds", 0.01)
    elif kind == "overall":
        monkeypatch.setattr(settings, "generation_timeout_seconds", 0.02)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://gateway.test"
    ) as cancellation_client:
        response = await cancellation_client.post(
            "/api/v1/generate/", headers=auth, json={"prompt": "private facts"}
        )
        if kind != "cancelled":
            assert response.status_code == 504
    row = db.session.scalars(select(Query)).one()
    final = db.session.scalars(select(FinalResponse)).one()
    assert (
        row.meta["status"]
        == final.meta["status"]
        == ("cancelled" if kind == "cancelled" else "failed")
    )
    assert row.meta["http_status"] == (499 if kind == "cancelled" else 504)
    assert row.meta["total_provider_cost_usd"] is None
    assert row.meta["usage_accounting_status"] == "not_applicable"
    assert provider.await_count == 1


async def test_rejected_requests_omit_bodies_and_do_not_trust_unverified_ownership(
    generation_records,
):
    client, auth, other, _, db, project = generation_records
    assert (
        await client.post("/api/v1/generate/", json={"prompt": "secret body"})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/generate/", headers=auth, json={"prompt": "", "secret": "secret body"}
        )
    ).status_code == 422
    rows = db.session.scalars(select(Query).order_by(Query.id)).all()
    assert (
        len(rows) == 2
        and rows[0].authorized_app_id is None
        and rows[1].authorized_app_id == project.id
    )
    for row in rows:
        assert row.raw_query == "" and row.meta["body_omitted"]
        assert row.meta["request_type"] == "generate" and row.meta["status"] == "rejected"
        assert "secret body" not in row.meta_fields
    response = await client.get("/api/v1/query/requests?status=rejected", headers=auth)
    assert response.json()["total"] == 1
    assert (await client.get("/api/v1/query/requests?status=rejected", headers=other)).json()[
        "total"
    ] == 0


async def test_existing_maintenance_accounts_success_once_and_recovers_interrupted_generation(
    generation_records,
):
    client, auth, _, _, db, _ = generation_records
    assert (
        await client.post("/api/v1/generate/", headers=auth, json={"prompt": "Hello"})
    ).status_code == 200
    row = db.session.scalars(select(Query)).one()
    assert row.meta["usage_accounting_status"] == "pending"
    await query_maintenance.maintain_queries(db)
    await query_maintenance.maintain_queries(db)
    db.session.expire_all()
    usage = db.session.scalars(select(ModelWebsiteUsage)).one()
    assert (usage.total_requests, usage.prompt_tokens, usage.completion_tokens) == (1, 30, 20)
    assert row.meta["usage_accounting_status"] == "recorded"
    interrupted = Query(
        raw_query="interrupted",
        authorized_app_id=row.authorized_app_id,
        created_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    interrupted.meta = {
        "request_type": "generate",
        "status": "processing",
        "usage_accounting_status": "not_applicable",
    }
    db.add(interrupted)
    await db.commit()
    await query_maintenance.maintain_queries(db)
    db.session.refresh(interrupted)
    assert interrupted.meta["status"] == "failed"
    assert interrupted.meta["error_type"] == "WorkerInterrupted"


async def test_shared_generation_rows_never_replace_or_displace_query_cache(generation_records):
    client, auth, _, _, db, project = generation_records
    query = Query(raw_query="same prompt", authorized_app_id=project.id)
    db.add(query)
    await db.flush()
    final = FinalResponse(
        query_id=query.id, generated_output="query cached answer", model_used="test"
    )
    final.meta = {"status": "completed", "query_hash": _md5("same prompt")}
    db.add(final)
    await db.commit()
    for _ in range(12):
        assert (
            await client.post("/api/v1/generate/", headers=auth, json={"prompt": "same prompt"})
        ).status_code == 200
    # Even if a generation record happened to acquire a query hash, exclude it
    # in SQL before the ten-row cache limit, preserving old query cache entries.
    newest = db.session.scalars(select(FinalResponse).order_by(FinalResponse.id.desc())).first()
    newest.meta = {**newest.meta, "query_hash": _md5("same prompt")}
    await db.commit()
    assert (
        await _check_cache("same prompt", db, project.id)
    ).generated_output == "query cached answer"


async def test_final_persistence_failure_does_not_return_success(generation_records, monkeypatch):
    client, auth, _, _, db, _ = generation_records
    original = db.commit

    async def fail_final():
        if any(isinstance(row, FinalResponse) for row in db.session.new):
            raise RuntimeError("private DB failure")
        await original()

    monkeypatch.setattr(db, "commit", fail_final)
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": "Hello"})
    assert (
        response.status_code == 503
        and response.json()["detail"]["code"] == "record_persistence_failed"
    )
    assert "private DB failure" not in response.text
    assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


async def test_fallback_records_all_attempts_but_accounts_only_success(generation_records):
    client, auth, _, provider, db, _ = generation_records
    failed = RuntimeError("secret upstream response")
    failed.usage = SimpleNamespace(prompt_tokens=99, completion_tokens=88)
    provider.side_effect = [failed, provider.return_value]
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": "Hello"})
    assert response.status_code == 200
    row = db.session.scalars(select(Query)).one()
    final = db.session.scalars(select(FinalResponse)).one()
    assert [a["status"] for a in final.meta["model_attempts"]] == ["failed", "completed"]
    assert final.meta["model_attempts"][0]["prompt_tokens"] == 99
    assert len(row.meta["provider_calls"]) == 2
    assert row.meta["total_provider_cost_usd"] == 0.03
    await query_maintenance.maintain_queries(db)
    usage = db.session.scalars(select(ModelWebsiteUsage)).one()
    assert (usage.total_requests, usage.prompt_tokens, usage.completion_tokens) == (1, 30, 20)
    assert "secret upstream response" not in row.meta_fields + final.meta_fields


async def test_actual_provider_attempts_stay_bounded_with_many_candidates(
    generation_records, monkeypatch
):
    client, auth, _, provider, db, _ = generation_records
    from app.services import generation

    for index in range(6):
        name = f"extra-chat-{index}"
        db.add(AIModel(name=name, provider="openai", is_active=True))
        monkeypatch.setitem(
            generation.litellm.model_cost,
            name,
            {
                "mode": "chat",
                "max_input_tokens": 128000,
                "max_output_tokens": 16384,
            },
        )
    await db.commit()
    provider.side_effect = RuntimeError("private provider failure")
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": "Hello"})
    assert response.status_code == 503 and provider.await_count == 4
    row = db.session.scalars(select(Query)).one()
    final = db.session.scalars(select(FinalResponse)).one()
    assert len(final.meta["model_attempts"]) == len(row.meta["provider_calls"]) == 4
    assert row.meta["total_provider_cost_usd"] is None
    assert "private provider failure" not in row.meta_fields + final.meta_fields
