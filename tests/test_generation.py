"""Direct generation: real isolated SQL/auth, mocked provider, no paid calls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api.v1 import generate
from app.core.config import settings
from app.core.middleware import limiter
from app.models.db_models import AIModel, AuthorizedApp, ProviderAPIKey, User
from app.services import generation, model_router
from app.services.api_key_service import generate_api_key
from httpx import ASGITransport, AsyncClient
from main import app


def result(text="Generated\ntext", reason="stop", **message):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=text, **message), finish_reason=reason)
        ]
    )


@pytest.fixture
async def setup_generation(unit_db, monkeypatch):
    owner = User(
        google_id="generation-owner",
        email="generation@example.test",
        is_active=True,
        api_access_enabled=True,
    )
    unit_db.session.add(owner)
    unit_db.session.flush()
    project = AuthorizedApp(
        name="generation",
        owner_user_id=owner.id,
        allowed_origin="https://generation.example.test",
        is_active=True,
        is_approved=True,
        allowed_endpoints_json=json.dumps(["POST /api/v1/generate/"]),
    )
    unit_db.session.add(project)
    for name in ("gpt-4o-mini", "gpt-4o"):
        unit_db.session.add(AIModel(name=name, provider="openai", is_active=True))
        monkeypatch.setitem(
            model_router.litellm.model_cost,
            name,
            {
                "mode": "chat",
                "max_input_tokens": 128000,
                "max_output_tokens": 16384,
            },
        )
    unit_db.session.add(
        ProviderAPIKey(provider="openai", label="fake", encrypted_key="fake-secret")
    )
    unit_db.session.commit()
    secret, _ = await generate_api_key(
        unit_db, client_name="generation", authorized_app_id=project.id
    )
    monkeypatch.setattr("app.services.key_manager.decrypt_token", lambda value: value)
    call = AsyncMock(return_value=result())
    monkeypatch.setattr(generation.litellm, "acompletion", call)
    monkeypatch.setattr(generate, "_slots", asyncio.Semaphore(settings.generation_max_concurrency))
    limiter.reset()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        yield client, {"X-API-Key": secret}, call, unit_db, project


@pytest.mark.parametrize(
    "text",
    [
        "Dear team,\nThank you. café",
        "Уважаемая команда,\nСпасибо.",
        "Estimado equipo,\nGracias.",
        "Chère équipe,\nMerci.",
        "Prezada equipe,\nObrigado. ação",
        "  emoji 🧑‍💻\n\nend  ",
    ],
)
async def test_unicode_multiline_prompt_only(setup_generation, monkeypatch, text):
    client, headers, call, _, _ = setup_generation
    call.return_value = result(text)
    # Neither the query dispatcher nor search/embeddings may run.
    forbidden = AsyncMock(side_effect=AssertionError("Search pipeline invoked"))
    monkeypatch.setattr("app.api.v1.query.router.process_query", forbidden)
    monkeypatch.setattr("app.services.embedding.embed", forbidden)
    response = await client.post(
        "/api/v1/generate/", json={"prompt": text}, headers={**headers, "X-Request-ID": "trace.123"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"answer": text, "request_id": "trace.123", "finish_reason": "stop"}
    assert response.headers["x-request-id"] == "trace.123"
    assert response.headers["cache-control"] == "no-store"
    kwargs = call.await_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": text}]
    assert kwargs["num_retries"] == kwargs["max_retries"] == 0
    assert kwargs["fallbacks"] == [] and kwargs["no-log"] is True
    assert "tools" not in kwargs and "functions" not in kwargs
    forbidden.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"prompt": ""},
        {"prompt": " \n\t"},
        {"prompt": 4},
        {"prompt": None},
        {"prompt": []},
        {"prompt": "personal data", "model": "secret"},
        {"prompt": "\ud800"},
        {"prompt": "x" * 32769},
    ],
)
async def test_validation_does_not_echo_or_log_input(setup_generation, body, caplog):
    client, headers, call, _, _ = setup_generation
    response = await client.post(
        "/api/v1/generate/",
        content=json.dumps(body),
        headers={**headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert "personal data" not in response.text + caplog.text
    assert "secret" not in response.text + caplog.text
    call.assert_not_awaited()


@pytest.mark.parametrize(
    "auth,status", [(None, 401), ("invalid", 401), ("forbidden", 403), ("valid", 200)]
)
async def test_real_service_auth_and_endpoint_grant(setup_generation, auth, status):
    client, headers, _, db, project = setup_generation
    if auth == "forbidden":
        project.allowed_endpoints_json = "[]"
        await db.commit()
    response = await client.post(
        "/api/v1/generate/",
        json={"prompt": "Hello"},
        headers={} if auth is None else {"X-API-Key": "wrong"} if auth == "invalid" else headers,
    )
    assert response.status_code == status, response.text


async def test_body_bytes_limit_with_and_without_content_length(setup_generation, monkeypatch):
    client, headers, call, _, _ = setup_generation
    monkeypatch.setattr(settings, "generation_max_body_bytes", 1024)
    response = await client.post("/api/v1/generate/", content=b"x" * 1025, headers=headers)
    assert response.status_code == 413

    async def chunks():
        yield b"x" * 700
        yield b"x" * 700

    response = await client.post("/api/v1/generate/", content=chunks(), headers=headers)
    assert response.status_code == 413
    call.assert_not_awaited()


async def test_large_prompt_is_not_truncated_and_ids_are_bounded(setup_generation):
    client, headers, call, _, _ = setup_generation
    prompt = "a" * settings.generation_max_prompt_chars
    response = await client.post(
        "/api/v1/generate/",
        json={"prompt": prompt},
        headers={**headers, "X-Request-ID": "bad id" * 100},
    )
    assert response.status_code == 200
    assert call.await_args.kwargs["messages"][0]["content"] == prompt
    assert len(response.json()["request_id"]) == 36
    assert response.headers["x-request-id"] == response.json()["request_id"]


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"max_input_tokens": 1000, "max_output_tokens": 4000},
        {"max_input_tokens": 128000, "max_output_tokens": 100},
    ],
)
async def test_unknown_or_insufficient_budgets_fail_before_dispatch(
    setup_generation, monkeypatch, info
):
    client, headers, call, _, _ = setup_generation
    for name in ("gpt-4o-mini", "gpt-4o"):
        monkeypatch.setitem(model_router.litellm.model_cost, name, {"mode": "chat", **info})
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_eligible_model"
    call.assert_not_awaited()


async def test_fallback_skips_small_context_and_uses_existing_order(setup_generation, monkeypatch):
    client, headers, call, _, _ = setup_generation
    monkeypatch.setitem(model_router.litellm.model_cost["gpt-4o-mini"], "max_input_tokens", 10)
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 200
    assert call.await_args.kwargs["model"] == "openai/gpt-4o"
    call.reset_mock()
    monkeypatch.setitem(model_router.litellm.model_cost["gpt-4o-mini"], "max_input_tokens", 128000)
    call.side_effect = [RuntimeError("sensitive upstream error"), result()]
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 200
    assert [c.kwargs["model"] for c in call.await_args_list] == [
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
    ]


@pytest.mark.parametrize(
    "provider_result,status,code",
    [
        (result("", "stop"), 502, "empty_provider_result"),
        (result(None, "content_filter"), 422, "provider_refusal"),
        (result("declined", refusal="declined"), 422, "provider_refusal"),
        (result("partial", "length"), 502, "output_truncated"),
        (result("tool", "tool_calls"), 502, "invalid_provider_result"),
        (SimpleNamespace(choices=[]), 502, "invalid_provider_result"),
        (result("usable", None), 200, None),
    ],
)
async def test_provider_outcomes_are_explicit(setup_generation, provider_result, status, code):
    client, headers, call, _, _ = setup_generation
    call.return_value = provider_result
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == status, response.text
    if code:
        assert response.json()["detail"]["code"] == code
        assert "answer" not in response.json()
    else:
        assert response.json()["finish_reason"] == "unknown"
    assert call.await_count == 1


async def test_timeout_never_retries_and_explicit_failures_are_bounded(setup_generation):
    client, headers, call, _, _ = setup_generation
    call.side_effect = TimeoutError("secret prompt and key")
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "provider_timeout"
    assert call.await_count == 1
    call.reset_mock()
    call.side_effect = RuntimeError("secret prompt and key")
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 503
    assert call.await_count == 2
    assert "secret" not in response.text


async def test_overall_deadline_and_concurrency_limit(setup_generation, monkeypatch):
    client, headers, call, _, _ = setup_generation

    async def slow(**kwargs):
        await asyncio.sleep(1)

    call.side_effect = slow
    monkeypatch.setattr(settings, "generation_timeout_seconds", 0.05)
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "request_timeout"
    assert not generate._slots.locked()
    monkeypatch.setattr(generate, "_slots", asyncio.Semaphore(0))
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "concurrency_limited"


async def test_rate_limit(setup_generation, monkeypatch):
    client, headers, _, _, _ = setup_generation
    monkeypatch.setattr(settings, "generation_rate_limit", "1/minute")
    first = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    second = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "rate_limited"


async def test_query_contract_retains_4096_limit():
    from app.api.v1.query.schemas import QueryRequest, QueryResponse
    from pydantic import ValidationError

    assert QueryRequest(query="x" * 4096).search_engine == "auto"
    with pytest.raises(ValidationError):
        QueryRequest(query="x" * 4097)
    assert set(QueryResponse.model_fields) == {"query_id", "answer", "routing", "sources"}


async def test_admin_can_discover_and_grant_new_endpoint(setup_generation, monkeypatch):
    client, _, _, _, project = setup_generation
    monkeypatch.setattr(settings, "cron_secret", "fake-admin")
    headers = {"X-Cron-Secret": "fake-admin"}
    response = await client.get("/api/v1/apps/endpoints", headers=headers)
    assert response.status_code == 200
    assert "POST /api/v1/generate/" in response.json()
    grants = ["POST /api/v1/query/", "POST /api/v1/generate/"]
    response = await client.patch(
        f"/api/v1/apps/{project.id}", headers=headers, json={"allowed_endpoints": grants}
    )
    assert response.status_code == 200, response.text
    assert response.json()["allowed_endpoints"] == grants


async def test_no_provider_key_does_not_use_environment_credentials(setup_generation):
    client, headers, call, db, _ = setup_generation
    from sqlalchemy import select

    key = db.session.scalars(select(ProviderAPIKey)).one()
    key.is_active = False
    await db.commit()
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "no_provider_key"
    call.assert_not_awaited()


async def test_provider_deadline_without_overall_timeout(setup_generation, monkeypatch):
    client, headers, call, _, _ = setup_generation

    async def slow(**kwargs):
        await asyncio.sleep(1)

    call.side_effect = slow
    monkeypatch.setattr(settings, "generation_provider_timeout_seconds", 0.01)
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "provider_timeout"
    assert call.await_count == 1


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (
            generation.litellm.APIConnectionError(
                message="secret", model="fake", llm_provider="openai"
            ),
            502,
            "provider_connection_error",
        ),
        (
            generation.litellm.ContentPolicyViolationError(
                message="secret", model="fake", llm_provider="openai"
            ),
            422,
            "provider_refusal",
        ),
    ],
)
async def test_ambiguous_connections_and_policy_failures_do_not_retry(
    setup_generation, failure, status, code
):
    client, headers, call, _, _ = setup_generation
    call.side_effect = failure
    response = await client.post("/api/v1/generate/", json={"prompt": "Hello"}, headers=headers)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert call.await_count == 1
    assert "secret" not in response.text
