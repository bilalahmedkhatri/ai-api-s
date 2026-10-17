# ruff: noqa: F811
"""App-scoped request history with isolated SQL and fake providers."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.v1.query.schemas import UnifiedSearchResult
from app.core.cache import _NoOpCache
from app.models.db_models import AuthorizedApp, ModelWebsiteUsage, Query, User
from app.services.api_key_service import generate_api_key
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import create_engine, inspect, select, text

from tests.test_query_async import chat_db  # noqa: F401


@pytest.fixture
async def records(chat_db, monkeypatch):
    owner = User(google_id="records", email="records@example.test", is_active=True, api_access_enabled=True)
    chat_db.add(owner)
    await chat_db.flush()
    headers = []
    for name in ("first", "second"):
        project = AuthorizedApp(owner_user_id=owner.id, name=name, allowed_origin="",
                                is_active=True, is_approved=True, allowed_endpoints_json=json.dumps([
                                    "POST /api/v1/query/", "GET /api/v1/query/requests",
                                    "GET /api/v1/query/requests/{query_id}",
                                ]))
        chat_db.add(project)
        await chat_db.flush()
        raw, _ = await generate_api_key(chat_db, name, project.id)
        headers.append({"X-API-Key": raw})
    monkeypatch.setattr("app.services.dispatcher.cache", _NoOpCache())
    monkeypatch.setattr("app.services.model_router.litellm.acompletion", AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="4"))],
        model="gpt-4o-mini-actual", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
    )))
    monkeypatch.setattr("app.services.model_router.litellm.completion_cost", lambda **kwargs: 0.01)
    return chat_db, headers


async def test_record_api_ownership_pagination_and_usage(records):
    db, headers = records
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2 + 2"})
        assert response.status_code == 200
        identifier = response.json()["query_id"]
        detail = await client.get(f"/api/v1/query/requests/{identifier}", headers=headers[0])
        assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store"
        data = detail.json()
        assert (data["input_tokens"], data["output_tokens"], data["cost_usd"]) == (3, 1, 0.01)
        assert data["model_used"] == "gpt-4o-mini-actual"
        assert data["request"]["status"] == "completed" and data["request"]["duration_ms"] >= 0
        assert data["request"]["request_id"] == response.headers["x-request-id"]
        assert data["routing"]["model_attempts"][0]["status"] == "completed"
        assert data["sources"] == []
        assert (await client.get(f"/api/v1/query/requests/{identifier}", headers=headers[1])).status_code == 404
        db.add(Query(raw_query="legacy private", input_type="text"))
        await db.commit()
        listing = await client.get("/api/v1/query/requests?page_size=1&status=completed", headers=headers[0])
        assert listing.json()["total"] == 1 and len(listing.json()["items"]) == 1
        assert (await client.get("/api/v1/query/requests?page=2&page_size=1", headers=headers[0])).json()["items"] == []
        assert (await client.get("/api/v1/query/requests", headers=headers[1])).json()["total"] == 0
        assert (await client.get("/api/v1/query/requests")).status_code == 401
        assert (await client.get("/api/v1/query/requests?page_size=101", headers=headers[0])).status_code == 422


async def test_early_failure_has_durable_safe_record(records, monkeypatch):
    _, headers = records
    monkeypatch.setattr("app.services.dispatcher.classify", AsyncMock(side_effect=RuntimeError("fake-secret")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "write a letter"})
        assert response.status_code == 503 and "fake-secret" not in response.text
        listing = await client.get("/api/v1/query/requests?status=failed", headers=headers[0])
        row = listing.json()["items"][0]
        assert row["request"]["error_type"] == "RuntimeError"
        assert row["input_tokens"] is None and row["cost_usd"] is None
        assert "fake-secret" not in listing.text


async def test_success_survives_accounting_failure_with_durable_tokens(records, monkeypatch):
    db, headers = records
    failed = AsyncMock(side_effect=RuntimeError("private accounting failure"))
    monkeypatch.setattr("app.services.model_router.record_model_usage", failed, raising=False)
    monkeypatch.setattr("app.services.query_maintenance.record_model_usage", failed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2 + 2"})
        assert response.status_code == 200 and response.json()["answer"] == "4"
        detail = await client.get(f"/api/v1/query/requests/{response.json()['query_id']}", headers=headers[0])
        data = detail.json()
        assert (data["input_tokens"], data["output_tokens"], data["cost_usd"]) == (3, 1, 0.01)
        assert data["routing"]["model_attempts"][0]["status"] == "completed"
        assert data["request"]["usage_accounting_status"] == "pending"
        assert "private accounting failure" not in detail.text
        assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


async def test_failed_attempt_usage_never_charges_user(records, monkeypatch):
    db, headers = records
    error = RuntimeError("private failed attempt")
    error.usage = SimpleNamespace(prompt_tokens=99, completion_tokens=77)
    success = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="4"))], model="gpt-4o",
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
    )
    monkeypatch.setattr("app.services.model_router.litellm.acompletion", AsyncMock(side_effect=[error, success]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2 + 2"})
        assert response.status_code == 200
        usage = db.session.scalars(select(ModelWebsiteUsage)).one()
        assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (3, 1, 4)
        # A fully failed request must not alter the successful user's totals.
        monkeypatch.setattr("app.services.model_router.litellm.acompletion", AsyncMock(side_effect=error))
        response = await client.post("/api/v1/query/", headers={**headers[0], "Cache-Control": "no-cache"},
                                     json={"query": "Calculate 3 + 3"})
        assert response.status_code == 503
        db.session.expire_all()
        usage = db.session.scalars(select(ModelWebsiteUsage)).one()
        assert (usage.total_requests, usage.total_tokens) == (1, 4)


async def test_last_used_failure_does_not_destroy_success(records, monkeypatch):
    _, headers = records
    monkeypatch.setattr("app.services.model_router.mark_key_used", AsyncMock(side_effect=RuntimeError("private DB failure")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2 + 2"})
        assert response.status_code == 200 and response.json()["answer"] == "4"
        detail = await client.get(f"/api/v1/query/requests/{response.json()['query_id']}", headers=headers[0])
        assert detail.json()["total_tokens"] == 4
        assert detail.json()["routing"]["model_attempts"][0]["key_bookkeeping_error_type"] == "RuntimeError"
        assert "private DB failure" not in detail.text


async def test_failed_final_persistence_never_charges_user(records, monkeypatch):
    db, headers = records
    monkeypatch.setattr("app.services.dispatcher._persist_final", AsyncMock(side_effect=RuntimeError("isolated persistence failure")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2 + 2"})
        assert response.status_code == 503
        assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


async def test_cancelled_request_is_never_completed_or_charged(records, monkeypatch):
    import asyncio

    db, headers = records
    monkeypatch.setattr("app.api.v1.query.router.process_query", AsyncMock(side_effect=asyncio.CancelledError()))
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        try:
            await client.post("/api/v1/query/", headers=headers[0], json={"query": "hello"})
        except asyncio.CancelledError:
            pass
        listing = await client.get("/api/v1/query/requests?status=cancelled", headers=headers[0])
        assert listing.json()["total"] == 1
        record = listing.json()["items"][0]
        assert record["status"] == record["routing"]["status"] == "cancelled"
        assert record["request"]["http_status"] == 499
        for status in ("completed", "failed", "processing"):
            assert (await client.get(f"/api/v1/query/requests?status={status}", headers=headers[0])).json()["total"] == 0
        assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


async def test_failure_after_final_commit_has_consistent_history_status(records, monkeypatch):
    db, headers = records
    fake = _NoOpCache()
    fake.set = AsyncMock(side_effect=RuntimeError("isolated post-commit failure"))
    monkeypatch.setattr("app.services.dispatcher.cache", fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2 + 2"})
        assert response.status_code == 503
        listing = (await client.get("/api/v1/query/requests?status=failed", headers=headers[0])).json()
        assert listing["total"] == 1
        record = listing["items"][0]
        assert record["status"] == record["routing"]["status"] == "failed"
        assert (await client.get("/api/v1/query/requests?status=completed", headers=headers[0])).json()["total"] == 0
        assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


@pytest.mark.parametrize("query,expected", [("hello", "Hello!"), ("thanks", "You're welcome!")])
async def test_direct_rules_return_answers_without_token_charges(records, monkeypatch, query, expected):
    db, headers = records
    provider = AsyncMock()
    monkeypatch.setattr("app.services.model_router.litellm.acompletion", provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": query})
        assert response.status_code == 200 and response.json()["answer"].startswith(expected)
        detail = await client.get(f"/api/v1/query/requests/{response.json()['query_id']}", headers=headers[0])
        assert detail.json()["total_tokens"] == detail.json()["cost_usd"] == 0
    provider.assert_not_awaited()
    assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


async def test_time_is_fresh_and_bypasses_both_caches(records, monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import Mock

    _, headers = records
    times = Mock(side_effect=[datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)])
    monkeypatch.setattr("app.services.rule_engine.datetime", SimpleNamespace(now=times))
    fake = _NoOpCache()
    fake.get, fake.set = AsyncMock(), AsyncMock()
    monkeypatch.setattr("app.services.dispatcher.cache", fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answers = []
        for _ in range(2):
            response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "current time"})
            assert response.status_code == 200 and not response.json()["routing"]["cache_hit"]
            answers.append(response.json()["answer"])
        assert answers[0] != answers[1] and all("UTC" in answer for answer in answers)
    fake.get.assert_not_awaited()
    fake.set.assert_not_awaited()


@pytest.mark.parametrize("query", ["latest news", "weather in Lahore", "price of coffee", "who is the CEO of Example"])
async def test_search_sources_survive_repeated_db_cache_hits(records, monkeypatch, query):
    db, headers = records
    source = UnifiedSearchResult(title="Latest report", url="https://example.test/report", snippet="Verified update", engine="tavily")
    search = AsyncMock(return_value=SimpleNamespace(engine="tavily", results=[source]))
    monkeypatch.setattr("app.services.dispatcher.search", search)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for index in range(3):
            response = await client.post("/api/v1/query/", headers=headers[0], json={"query": query})
            data = response.json()
            assert response.status_code == 200 and not data["answer"].startswith("[Rule-matched:")
            assert data["sources"] == [source.model_dump()]
            assert data["routing"]["cache_hit"] is (index > 0)
            assert data["routing"]["engine_used"] == "tavily"
            detail = await client.get(f"/api/v1/query/requests/{data['query_id']}", headers=headers[0])
            assert detail.json()["sources"] == [source.model_dump()]
    search.assert_awaited_once()
    usage = db.session.scalars(select(ModelWebsiteUsage)).one()
    assert (usage.total_requests, usage.total_tokens) == (1, 4)


async def test_missing_search_results_are_not_fabricated_or_cached(records, monkeypatch):
    _, headers = records
    source = UnifiedSearchResult(title="Report", url="https://example.test/report", snippet="Verified", engine="tavily")
    search = AsyncMock(side_effect=[SimpleNamespace(engine="tavily", results=[]), SimpleNamespace(engine="tavily", results=[source])])
    monkeypatch.setattr("app.services.dispatcher.search", search)
    fake = _NoOpCache()
    fake.set = AsyncMock()
    monkeypatch.setattr("app.services.dispatcher.cache", fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/query/", headers=headers[0], json={"query": "weather in Lahore"})
        assert first.status_code == 200 and "couldn't retrieve" in first.json()["answer"]
        fake.set.assert_not_awaited()
        second = await client.post("/api/v1/query/", headers=headers[0], json={"query": "weather in Lahore"})
        assert second.status_code == 200 and second.json()["sources"] == [source.model_dump()]
        assert not second.json()["routing"]["cache_hit"]
    assert search.await_count == 2


async def test_redis_and_db_cache_hits_have_records_and_app_scope(records, monkeypatch):
    _, headers = records
    from app.services.dispatcher import _md5

    fake = _NoOpCache()
    seen = []

    async def get(key):
        seen.append(key)
        return {"answer": "cached", "model_used": "original-model", "engine_used": "tavily",
                "sources": [{"title": "Report", "url": "https://example.test/report", "snippet": "Verified", "engine": "tavily"}]}

    fake.get = get
    monkeypatch.setattr("app.services.dispatcher.cache", fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for auth in headers:
            response = await client.post("/api/v1/query/", headers=auth, json={"query": "hello"})
            detail = await client.get(f"/api/v1/query/requests/{response.json()['query_id']}", headers=auth)
            assert detail.json()["routing"]["cache_hit"]
            assert detail.json()["input_tokens"] == detail.json()["output_tokens"] == 0
            assert detail.json()["cost_usd"] == 0
            assert len(detail.json()["sources"]) == 1
            assert detail.json()["routing"]["engine_used"] == "tavily"
        assert seen[0] != seen[1] and all(key.endswith(_md5("hello")) for key in seen)
        monkeypatch.setattr("app.services.dispatcher.cache", _NoOpCache())
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "hello"})
        assert response.json()["routing"]["cache_hit"]
        detail = await client.get(f"/api/v1/query/requests/{response.json()['query_id']}", headers=headers[0])
        assert detail.json()["model_used"] == "original-model"
        assert len(detail.json()["sources"]) == 1


def test_query_ownership_migration_preserves_legacy_and_downgrades(tmp_path):
    path = Path(__file__).parents[1] / "alembic/versions/d920f41a672e_query_request_ownership.py"
    spec = importlib.util.spec_from_file_location("query_ownership_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'ownership.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE authorized_apps (id INTEGER PRIMARY KEY, allowed_endpoints_json TEXT)"))
            conn.execute(text("INSERT INTO authorized_apps VALUES (1, :grants), (2, '[]')"),
                         {"grants": json.dumps(["POST /api/v1/query/"])})
            conn.execute(text("CREATE TABLE queries (id INTEGER PRIMARY KEY, raw_query TEXT NOT NULL)"))
            conn.execute(text("INSERT INTO queries VALUES (1, 'legacy')"))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                assert conn.execute(text("SELECT authorized_app_id FROM queries WHERE id=1")).scalar() is None
                assert inspect(conn).get_foreign_keys("queries")[0]["referred_table"] == "authorized_apps"
                grants = json.loads(conn.execute(text("SELECT allowed_endpoints_json FROM authorized_apps WHERE id=1")).scalar())
                assert set(migration.READ_GRANTS) <= set(grants)
                assert conn.execute(text("SELECT allowed_endpoints_json FROM authorized_apps WHERE id=2")).scalar() == "[]"
                migration.downgrade()
                assert conn.execute(text("SELECT raw_query FROM queries WHERE id=1")).scalar() == "legacy"
    finally:
        engine.dispose()
