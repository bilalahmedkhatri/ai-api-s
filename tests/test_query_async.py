"""Async query provider calls; fake providers and isolated database fixtures only."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.cache import _NoOpCache
from app.models.db_models import (
    AIModel,
    AuthorizedApp,
    FinalResponse,
    ModelWebsiteUsage,
    ProviderAPIKey,
    User,
)
from app.services import embedding, model_router
from app.services.api_key_service import generate_api_key
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select


@pytest.fixture
def chat_db(unit_db, monkeypatch):
    for name in ("gpt-4o-mini", "gpt-4o"):
        unit_db.session.add(AIModel(name=name, provider="openai", is_active=True, is_free=False))
        monkeypatch.setitem(model_router.litellm.model_cost, name, {"mode": "chat"})
    unit_db.session.add(ProviderAPIKey(provider="openai", label="fake", encrypted_key="fake-secret"))
    unit_db.session.commit()
    monkeypatch.setattr("app.services.key_manager.decrypt_token", lambda value: value)
    return unit_db


async def test_llm_requests_overlap_and_keep_routing_and_usage(chat_db, monkeypatch):
    ready = asyncio.Event()
    started = 0

    async def provider(**kwargs):
        nonlocal started
        started += 1
        if started == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=2)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" answer "))],
            model="fake-actual-model",
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    call = AsyncMock(side_effect=provider)
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    sync = Mock(side_effect=AssertionError("Synchronous provider call"))
    monkeypatch.setattr(model_router.litellm, "completion", sync)
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0.01)
    results = await asyncio.gather(
        model_router.call_llm("first", "coding", db=chat_db, max_tokens=16),
        model_router.call_llm("second", "general", db=chat_db, force_model="openai/gpt-4o"),
    )
    assert call.await_count == 2
    sync.assert_not_called()
    first, second = [c.kwargs for c in call.await_args_list]
    assert first["model"] == "openai/gpt-4o-mini" and first["max_tokens"] == 16
    assert first["api_key"] == "fake-secret" and first["num_retries"] == 0
    assert first["timeout"] == model_router.LLM_TIMEOUT
    assert "fallbacks" not in first
    assert second["model"] == "openai/gpt-4o"
    for result in results:
        assert (result.answer, result.model_used) == ("answer", "fake-actual-model")
        assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (3, 2, 0.01)


async def test_llm_async_failure_propagates(chat_db, monkeypatch):
    call = AsyncMock(side_effect=TimeoutError("isolated timeout"))
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    with pytest.raises(model_router.ModelRoutingError) as error:
        await model_router.call_llm("test", "general", db=chat_db)
    assert call.await_count == 2
    assert all(a["error_type"] == "TimeoutError" for a in error.value.attempts)


async def test_embeddings_await_provider_and_skip_empty_input(monkeypatch):
    call = AsyncMock(return_value=SimpleNamespace(data=[{"embedding": [0.1, 0.2]}]))
    monkeypatch.setattr(embedding.litellm, "aembedding", call)
    sync = Mock(side_effect=AssertionError("Synchronous embedding call"))
    monkeypatch.setattr(embedding.litellm, "embedding", sync)
    monkeypatch.setattr(embedding, "_model", lambda: "fake/embedding")
    assert await embedding.embed([]) == []
    call.assert_not_awaited()
    assert await embedding.embed(["test"]) == [[0.1, 0.2]]
    call.assert_awaited_once_with(model="fake/embedding", input=["test"], timeout=15, num_retries=0)
    sync.assert_not_called()


async def test_db_selection_filters_inactive_and_nonchat_without_hiding_fallbacks(chat_db, monkeypatch):
    chat_db.session.add_all([
        AIModel(name="disabled", provider="openai", is_active=False),
        AIModel(name="embedding-only", provider="openai"),
        AIModel(name="unknown-capability", provider="openai"),
    ])
    monkeypatch.setitem(model_router.litellm.model_cost, "embedding-only", {"mode": "embedding"})
    for index in range(6):
        name = f"fake-chat-{index}"
        chat_db.session.add(AIModel(name=name, provider="openai"))
        monkeypatch.setitem(model_router.litellm.model_cost, name, {"mode": "chat"})
    chat_db.session.commit()
    rows = await model_router._pick_models(chat_db, "openai/gpt-4o-mini")
    assert len(rows) == 8
    assert rows[0].name == "gpt-4o-mini"
    assert not {"disabled", "embedding-only", "unknown-capability"} & {r.name for r in rows}


async def test_replicate_gpt_is_second_and_openrouter_is_excluded(chat_db, monkeypatch):
    monkeypatch.setitem(model_router.litellm.model_cost, "openrouter/openai/gpt-4o", {"mode": "chat"})
    chat_db.session.add_all([
        AIModel(name="openai/gpt-4o-mini", provider="replicate", is_free=False),
        AIModel(name="openrouter/openai/gpt-4o", provider="openrouter"),
        ProviderAPIKey(provider="replicate", label="replicate", encrypted_key="replicate-secret"),
        ProviderAPIKey(provider="openrouter", label="openrouter", encrypted_key="openrouter-secret"),
    ])
    chat_db.session.commit()
    rows = await model_router._pick_models(chat_db, "openai/gpt-4o-mini")
    assert [model_router._model_name(m) for m in rows[:2]] == ["openai/gpt-4o-mini", "replicate/openai/gpt-4o-mini"]
    assert not any(m.provider == "openrouter" for m in rows)
    success = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], model="hosted-gpt")
    call = AsyncMock(side_effect=[RuntimeError("primary failed"), success])
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0)
    result = await model_router.call_llm("test", "general", db=chat_db)
    assert result.answer == "ok"
    assert [c.kwargs["model"] for c in call.await_args_list] == ["openai/gpt-4o-mini", "replicate/openai/gpt-4o-mini"]
    assert call.await_args_list[1].kwargs["api_key"] == "replicate-secret"
    chat_db.session.expire_all()
    excluded = chat_db.session.scalars(select(ProviderAPIKey).where(ProviderAPIKey.provider == "openrouter")).one()
    assert excluded.requests_today == 0


async def test_missing_keys_do_not_consume_four_call_budget(chat_db, monkeypatch):
    for index in range(4):
        name = f"no-key-chat-{index}"
        chat_db.session.add(AIModel(name=name, provider="groq", is_free=True))
        monkeypatch.setitem(model_router.litellm.model_cost, name, {"mode": "chat"})
    chat_db.session.commit()
    success = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], model="gpt-4o-mini")
    call = AsyncMock(return_value=success)
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0)
    result = await model_router.call_llm("test", "general", db=chat_db, force_model="absent")
    assert [a["status"] for a in result.attempts] == ["skipped"] * 4 + ["completed"]
    call.assert_awaited_once()


async def test_actual_provider_calls_remain_bounded(chat_db, monkeypatch):
    for index in range(5):
        name = f"extra-chat-{index}"
        chat_db.session.add(AIModel(name=name, provider="openai"))
        monkeypatch.setitem(model_router.litellm.model_cost, name, {"mode": "chat"})
    chat_db.session.commit()
    call = AsyncMock(side_effect=TimeoutError("isolated timeout"))
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    with pytest.raises(model_router.ModelRoutingError):
        await model_router.call_llm("test", "general", db=chat_db)
    assert call.await_count == model_router.MAX_MODEL_ATTEMPTS
    chat_db.session.expire_all()
    assert chat_db.session.scalars(select(ProviderAPIKey)).one().requests_today == model_router.MAX_MODEL_ATTEMPTS


async def test_primary_failure_uses_db_fallback_and_records_real_usage(chat_db, monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="fallback answer"))],
        model="gpt-4o-snapshot", usage=SimpleNamespace(prompt_tokens=5, completion_tokens=4),
    )
    call = AsyncMock(side_effect=[RuntimeError("fake-secret must not be recorded"), response])
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0.02)
    result = await model_router.call_llm("test", "general", db=chat_db)
    assert [c.kwargs["model"] for c in call.await_args_list] == ["openai/gpt-4o-mini", "openai/gpt-4o"]
    assert result.model_used == "gpt-4o-snapshot"
    assert [a["status"] for a in result.attempts] == ["failed", "completed"]
    assert "fake-secret" not in str(result.attempts)
    # Low-level calls do not charge users; only completed request records do.
    assert chat_db.session.scalars(select(ModelWebsiteUsage)).all() == []
    key = chat_db.session.scalars(select(ProviderAPIKey)).one()
    chat_db.session.expire_all()
    assert key.requests_today == 2 and key.total_count == 0


async def test_no_key_never_uses_environment_credentials(chat_db, monkeypatch):
    chat_db.session.scalars(select(ProviderAPIKey)).one().is_active = False
    chat_db.session.commit()
    call = AsyncMock()
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    with pytest.raises(model_router.ModelRoutingError) as error:
        await model_router.call_llm("test", "general", db=chat_db)
    assert all(a["error_type"] == "NoEligibleKey" for a in error.value.attempts)
    call.assert_not_awaited()


async def test_concurrent_chat_calls_cannot_overrun_one_remaining_request(chat_db, monkeypatch):
    key = chat_db.session.scalars(select(ProviderAPIKey)).one()
    key.daily_limit = 1
    chat_db.session.commit()
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(**kwargs):
        started.set()
        await release.wait()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], model="gpt-4o-mini")

    call = AsyncMock(side_effect=provider)
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0)
    first = asyncio.create_task(model_router.call_llm("first", "general", db=chat_db))
    await asyncio.wait_for(started.wait(), 2)
    second = asyncio.create_task(model_router.call_llm("second", "general", db=chat_db))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert call.await_count == 1
    assert sum(isinstance(r, model_router.LLMResult) for r in results) == 1
    chat_db.session.expire_all()
    assert chat_db.session.get(ProviderAPIKey, key.id).requests_today == 1


def test_atomic_reservations_across_independent_sessions(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.models.db_models import Base
    from app.services.key_manager import get_next_available_key
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from tests.test_app_authorization import AsyncAdapter

    engine = create_engine(f"sqlite:///{tmp_path / 'quota.db'}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all([ProviderAPIKey(provider="openai", label=str(i), encrypted_key="fake", daily_limit=1)
                             for i in range(3)])
            session.commit()
        monkeypatch.setattr("app.services.key_manager.decrypt_token", lambda value: value)

        def reserve(_):
            with Session(engine, expire_on_commit=False) as session:
                return asyncio.run(get_next_available_key(AsyncAdapter(session), "openai", reserve=True))

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(reserve, range(24)))
        assert sum(result is not None for result in results) == 3
        with Session(engine) as session:
            assert list(session.scalars(select(ProviderAPIKey.requests_today))) == [1, 1, 1]
            assert list(session.scalars(select(ProviderAPIKey.total_count))) == [0, 0, 0]
    finally:
        engine.dispose()


async def test_reservation_compiles_with_postgres_quota_guard(chat_db, monkeypatch):
    from app.services.key_manager import get_next_available_key
    from sqlalchemy.dialects import postgresql

    statements = []
    execute = chat_db.execute

    async def capture(statement):
        if statement.is_update:
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return await execute(statement)

    monkeypatch.setattr(chat_db, "execute", capture)
    assert await get_next_available_key(chat_db, "openai", reserve=True)
    sql = statements[0]
    assert "requests_today=(provider_api_keys.requests_today +" in sql
    assert "provider_api_keys.requests_today < provider_api_keys.daily_limit" in sql
    assert "RETURNING provider_api_keys.id" in sql
    assert "total_count=" not in sql


async def test_rate_limit_marks_exhausted_key_and_fallback_uses_next(chat_db, monkeypatch):
    first = chat_db.session.scalars(select(ProviderAPIKey)).one()
    first.daily_limit = 1
    first.priority = 1
    chat_db.session.add(ProviderAPIKey(provider="openai", label="second", encrypted_key="second-secret", priority=2))
    chat_db.session.commit()
    error = RuntimeError("private provider failure")
    error.status_code = 429
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], model="gpt-4o")
    call = AsyncMock(side_effect=[error, response])
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0)
    result = await model_router.call_llm("test", "general", db=chat_db)
    assert result.answer == "ok" and result.attempts[0]["status_code"] == 429
    assert [c.kwargs["api_key"] for c in call.await_args_list] == ["fake-secret", "second-secret"]
    chat_db.session.expire_all()
    assert chat_db.session.get(ProviderAPIKey, first.id).requests_today == 1


async def test_endpoint_failures_are_recorded_safely_and_not_cached(chat_db, monkeypatch):
    import json

    owner = User(google_id="query-test", email="query@example.test", is_active=True, api_access_enabled=True)
    chat_db.add(owner)
    await chat_db.flush()
    project = AuthorizedApp(owner_user_id=owner.id, name="Query test", allowed_origin="",
                            allowed_endpoints_json=json.dumps(["POST /api/v1/query/"]),
                            is_active=True, is_approved=True)
    chat_db.add(project)
    await chat_db.flush()
    key, _ = await generate_api_key(chat_db, "Query test", project.id)
    cache = _NoOpCache()
    cache.set = AsyncMock()
    monkeypatch.setattr("app.services.dispatcher.cache", cache)
    call = AsyncMock(side_effect=TimeoutError("fake-secret"))
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            response = await client.post("/api/v1/query/", json={"query": "Calculate 2 + 2"},
                                         headers={"X-API-Key": key})
            assert response.status_code == 503 and "fake-secret" not in response.text
    assert call.await_count == 4
    cache.set.assert_not_awaited()
    rows = chat_db.session.scalars(select(FinalResponse)).all()
    assert len(rows) == 2
    assert all(r.meta["status"] == "failed" and len(r.meta["model_attempts"]) == 2 for r in rows)
    assert all("fake-secret" not in r.meta_fields for r in rows)
    call.side_effect = None
    call.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="4"))],
        model="gpt-4o-mini-actual", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
    )
    monkeypatch.setattr(model_router.litellm, "completion_cost", lambda **kwargs: 0.01)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            response = await client.post("/api/v1/query/", json={"query": "Calculate 2 + 2"},
                                         headers={"X-API-Key": key, "Cache-Control": "no-cache"})
            assert response.status_code == 200 and response.json()["answer"] == "4"
            assert response.json()["routing"]["model_used"] == "gpt-4o-mini-actual"
    assert call.await_count == 6  # Both no-cache requests reached the provider.
    assert cache.set.await_count == 2
    completed = [r for r in chat_db.session.scalars(select(FinalResponse)) if r.meta["status"] == "completed"]
    assert len(completed) == 2
    assert all((r.prompt_tokens, r.completion_tokens, r.cost_usd) == (3, 1, 0.01) for r in completed)
    assert all(r.meta["model_attempts"][0]["status"] == "completed" for r in completed)
    usage = chat_db.session.scalars(select(ModelWebsiteUsage)).one()
    assert (usage.total_requests, usage.total_tokens) == (2, 8)
