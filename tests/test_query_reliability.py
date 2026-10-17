# ruff: noqa: F811
"""Isolated request auditing, recovery, estimates and exactly-once accounting."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.db_models import FinalResponse, ModelWebsiteUsage, Query
from app.services import query_maintenance, search_aggregator
from app.services.embedding import embed
from app.services.query_telemetry import cost_summary, provider_calls
from app.services.usage_tracker import record_model_usage
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

from tests.test_dashboard import dashboard  # noqa: F401
from tests.test_query_async import chat_db  # noqa: F401
from tests.test_query_records import records  # noqa: F401


async def test_rejections_omit_body_and_unverified_ownership(records):
    db, headers = records
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/query/", json={"query": "private-body"})).status_code == 401
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "", "secret": "private-body"})
        assert response.status_code == 422 and "private-body" not in response.text
        listing = (await client.get("/api/v1/query/requests?status=rejected", headers=headers[0])).json()
        assert listing["total"] == 1 and listing["items"][0]["input"] == ""
        assert listing["items"][0]["request"]["body_omitted"]
        assert (await client.get("/api/v1/query/requests?status=rejected", headers=headers[1])).json()["total"] == 0
    rows = db.session.scalars(select(Query).order_by(Query.id)).all()
    assert len(rows) == 2 and rows[0].authorized_app_id is None
    assert "private-body" not in json.dumps([r.meta for r in rows])


async def test_pending_accounting_recovers_original_day_once(records, monkeypatch):
    db, headers = records
    monkeypatch.setattr(query_maintenance, "record_model_usage", AsyncMock(side_effect=RuntimeError("fake-secret")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2+2"})
        assert response.status_code == 200
    row = db.session.get(Query, response.json()["query_id"])
    assert row.meta["usage_accounting_status"] == "pending"
    original_date = datetime.now(UTC) - timedelta(days=1)
    row.created_at = original_date
    await db.commit()
    monkeypatch.setattr(query_maintenance, "record_model_usage", record_model_usage)
    await query_maintenance.maintain_queries(db)
    await query_maintenance.maintain_queries(db)
    assert not await query_maintenance.account_query(db, row.id)
    usage = db.session.scalars(select(ModelWebsiteUsage)).one()
    assert usage.usage_date == original_date.date()
    assert (usage.total_requests, usage.total_tokens) == (1, 4)
    assert row.meta["usage_accounting_status"] == "recorded"


async def test_accounting_partial_write_rolls_back_before_retry(records, monkeypatch):
    db, headers = records
    async def fail_after_upsert(*args, **kwargs):
        await record_model_usage(*args, **kwargs)
        raise RuntimeError("crash before transaction commit")
    monkeypatch.setattr(query_maintenance, "record_model_usage", fail_after_upsert)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2+2"})
        assert response.status_code == 200
    assert db.session.scalars(select(ModelWebsiteUsage)).all() == []
    monkeypatch.setattr(query_maintenance, "record_model_usage", record_model_usage)
    assert await query_maintenance.account_query(db, response.json()["query_id"])
    assert not await query_maintenance.account_query(db, response.json()["query_id"])
    assert db.session.scalars(select(ModelWebsiteUsage)).one().total_tokens == 4


async def test_recovery_does_not_retry_generation_or_charge_stale_success(unit_db):
    old = datetime.now(UTC) - timedelta(minutes=20)
    stale = Query(raw_query="interrupted", created_at=old)
    stale.meta = {"status": "processing"}
    fresh = Query(raw_query="running")
    fresh.meta = {"status": "processing"}
    unit_db.add(stale)
    unit_db.add(fresh)
    await unit_db.flush()
    final = FinalResponse(query_id=stale.id, generated_output="unfinalized", model_used="gpt")
    final.meta = {"status": "completed"}
    unit_db.add(final)
    await unit_db.commit()
    await query_maintenance.maintain_queries(unit_db)
    await query_maintenance.maintain_queries(unit_db)
    unit_db.session.expire_all()
    assert stale.meta["status"] == final.meta["status"] == "failed"
    assert stale.meta["error_type"] == "WorkerInterrupted"
    assert stale.meta["total_provider_cost_usd"] is None
    assert fresh.meta["status"] == "processing"
    assert unit_db.session.scalars(select(ModelWebsiteUsage)).all() == []
    assert len(unit_db.session.scalars(select(FinalResponse)).all()) == 1


async def test_failed_provider_tokens_are_observed_but_never_billed(records, monkeypatch):
    db, headers = records
    error = RuntimeError("fake-secret")
    error.usage = {"prompt_tokens": 99, "completion_tokens": 77}
    success = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="4"))],
                              model="actual", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1))
    monkeypatch.setattr("app.services.model_router.litellm.acompletion", AsyncMock(side_effect=[error, success]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "Calculate 2+2"})
        data = (await client.get(f"/api/v1/query/requests/{response.json()['query_id']}", headers=headers[0])).json()
    calls = data["request"]["provider_calls"]
    assert calls[0]["status"] == "failed" and calls[0]["prompt_tokens"] == 99
    assert calls[1]["status"] == "completed"
    assert data["request"]["total_provider_cost_usd"] == 0.02
    assert db.session.scalars(select(ModelWebsiteUsage)).one().total_tokens == 4
    assert "fake-secret" not in json.dumps(data)


async def test_embedding_and_search_costs_preserve_unknowns(monkeypatch):
    calls = []
    token = provider_calls.set(calls)
    monkeypatch.setattr("app.services.embedding.litellm.aembedding", AsyncMock(return_value=SimpleNamespace(
        data=[{"embedding": [1]}], usage={"prompt_tokens": 7})))
    monkeypatch.setattr("app.services.embedding.litellm.completion_cost", lambda **kwargs: 0.002)
    monkeypatch.setattr(search_aggregator.settings, "tavily_api_key", "test")
    monkeypatch.setattr(search_aggregator.settings, "brave_search_cost_usd", 0.01)
    monkeypatch.setattr(search_aggregator, "search_tavily", AsyncMock(side_effect=RuntimeError("fake-secret")))
    monkeypatch.setattr(search_aggregator, "search_brave", AsyncMock(return_value="search result"))
    try:
        assert await embed(["test"]) == [[1]]
        assert await search_aggregator.search("test") == "search result"
    finally:
        provider_calls.reset(token)
    assert [c["kind"] for c in calls] == ["embedding", "search", "search"]
    summary = cost_summary(calls)
    assert summary["known_provider_cost_usd"] == 0.012
    assert summary["total_provider_cost_usd"] is None and summary["unknown_cost_count"] == 1
    assert "fake-secret" not in json.dumps(summary)
    assert cost_summary([])["total_provider_cost_usd"] == 0


async def test_telemetry_is_isolated_between_concurrent_requests():
    async def capture(name):
        calls = []
        token = provider_calls.set(calls)
        try:
            await asyncio.sleep(0)
            provider_calls.get().append({"model": name})
            return calls
        finally:
            provider_calls.reset(token)
    assert await asyncio.gather(capture("first"), capture("second")) == [[{"model": "first"}], [{"model": "second"}]]
    assert provider_calls.get() is None


async def test_deadline_finalizes_failed_without_billing(records, monkeypatch):
    db, headers = records
    async def stalled(*args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr("app.api.v1.query.router.QUERY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.api.v1.query.router.process_query", stalled)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/query/", headers=headers[0], json={"query": "test"})
        assert response.status_code == 503
    row = db.session.scalars(select(Query)).one()
    assert row.meta["status"] == "failed" and row.meta["error_type"] == "TimeoutError"
    assert row.meta["total_provider_cost_usd"] is None
    assert db.session.scalars(select(ModelWebsiteUsage)).all() == []


async def test_admin_request_history_denies_non_admin(dashboard):
    db, member, headers = dashboard
    row = Query(raw_query="admin only")
    row.meta = {"status": "rejected"}
    db.add(row)
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/query-requests?status=rejected", headers=headers)
        assert response.status_code == 200 and response.json()["total"] == 1
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get(f"/api/v1/dashboard/query-requests/{row.id}", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/dashboard/query-requests/9999", headers=headers)).status_code == 404
        member.role = "viewer"
        await db.commit()
        assert (await client.get("/api/v1/dashboard/query-requests", headers=headers)).status_code == 403
        assert (await client.get(f"/api/v1/dashboard/query-requests/{row.id}", headers=headers)).status_code == 403
        assert (await client.get("/api/v1/dashboard/query-requests")).status_code == 401


def test_concurrent_accounting_replays_count_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from app.models.db_models import AIModel, AuthorizedApp, Base, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from tests.test_app_authorization import AsyncAdapter

    engine = create_engine(f"sqlite:///{tmp_path / 'accounting.db'}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(google_id="test", email="accounting@example.test")
            session.add(owner)
            session.flush()
            project = AuthorizedApp(owner_user_id=owner.id, name="test", allowed_origin="https://example.test")
            session.add(project)
            session.add(AIModel(name="test-chat", provider="test", is_free=True))
            session.flush()
            row = Query(raw_query="test", authorized_app_id=project.id)
            row.meta = {"status": "completed", "usage_accounting_status": "pending"}
            session.add(row)
            session.flush()
            identifier = row.id
            final = FinalResponse(query_id=identifier, generated_output="ok", model_used="test-chat")
            final.meta = {"status": "completed", "model_attempts": [{"status": "completed",
                          "catalog_name": "test-chat", "provider": "test", "is_free": True,
                          "prompt_tokens": 3, "completion_tokens": 1}]}
            session.add(final)
            session.commit()

        def replay(_):
            with Session(engine, expire_on_commit=False) as session:
                return asyncio.run(query_maintenance.account_query(AsyncAdapter(session), identifier))
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(replay, range(24)))
        assert sum(results) == 1
        with Session(engine) as session:
            usage = session.scalars(select(ModelWebsiteUsage)).one()
            assert (usage.total_requests, usage.total_tokens) == (1, 4)
            assert session.get(Query, identifier).meta["usage_accounting_status"] == "recorded"
    finally:
        engine.dispose()
