"""Provider key claims use isolated databases and fake secrets only."""
import asyncio
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import settings
from app.models.db_models import ProviderAPIKey
from app.services import key_manager
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from tests.test_app_authorization import AsyncAdapter

PATH = "/api/v1/keys/provider/GEMINI/claim"
ADMIN = {"X-Cron-Secret": "isolated-claim-secret"}


@pytest.fixture
def pool(unit_db, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", ADMIN["X-Cron-Secret"])

    def decrypt(value):
        if not value.startswith("enc:"):
            raise ValueError("invalid stored credential")
        return value[4:]

    monkeypatch.setattr(key_manager, "decrypt_token", decrypt)

    async def rollback():
        unit_db.session.rollback()

    unit_db.rollback = rollback
    return unit_db


def add(db, label, **kwargs):
    row = ProviderAPIKey(provider=kwargs.pop("provider", "gemini"), label=label,
                         encrypted_key="enc:fake-key-" + label, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


def counts(db):
    db.session.expire_all()
    return {r.label: (r.total_count, r.requests_today) for r in db.session.scalars(select(ProviderAPIKey))}


async def test_rank_filter_and_increment(pool):
    add(pool, "other", provider="groq")
    add(pool, "inactive", is_active=False)
    add(pool, "quota", daily_limit=1, requests_today=1)
    add(pool, "high", total_count=20, requests_today=0)
    add(pool, "second", total_count=2, requests_today=7)
    add(pool, "first", total_count=2, requests_today=3)
    assert await key_manager.claim_provider_keys(pool, " GEMINI ") == ["fake-key-first", "fake-key-second"]
    result = counts(pool)
    assert result["first"] == (3, 4)
    assert result["second"] == (3, 8)
    assert result["other"] == result["inactive"] == (0, 0)
    assert result["quota"] == (0, 1)
    assert result["high"] == (20, 0)


async def test_priority_then_newest_id_break_ties(pool):
    add(pool, "older", priority=1)
    add(pool, "newer", priority=1)
    add(pool, "low-priority", priority=2)
    assert await key_manager.claim_provider_keys(pool, "gemini") == ["fake-key-newer", "fake-key-older"]


async def test_one_key_and_quota_boundary(pool):
    add(pool, "last", daily_limit=1)
    assert await key_manager.claim_provider_keys(pool, "gemini") == ["fake-key-last"]
    assert await key_manager.claim_provider_keys(pool, "gemini") == []
    assert counts(pool)["last"] == (1, 1)


async def test_daily_reset_preserves_total(pool):
    add(pool, "keep", total_count=8, requests_today=3)
    await key_manager.reset_daily_counts(pool, "gemini")
    assert counts(pool)["keep"] == (8, 0)


async def test_cron_reset_is_post_only_and_provider_scoped(pool):
    add(pool, "selected", total_count=8, requests_today=3)
    add(pool, "other", provider="groq", total_count=9, requests_today=4)
    path = "/api/v1/cron/reset-daily-keys"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path, headers=ADMIN)).status_code == 405
        assert (await client.post(path, params={"provider": "gemini"})).status_code == 401
        for provider in ("", " ", "gemini/groq"):
            assert (await client.post(path, params={"provider": provider}, headers=ADMIN)).status_code == 422
        assert counts(pool) == {"selected": (8, 3), "other": (9, 4)}
        response = await client.post(path, params={"provider": "GEMINI"}, headers=ADMIN)
        assert response.status_code == 200
        assert response.json()["keys_affected"] == 1
        assert counts(pool) == {"selected": (8, 0), "other": (9, 4)}
        response = await client.post(path, params={"provider": "unknown"}, headers=ADMIN)
        assert response.status_code == 200
        assert response.json()["keys_affected"] == 0
        assert counts(pool) == {"selected": (8, 0), "other": (9, 4)}
        response = await client.post(path, headers=ADMIN)
        assert response.status_code == 200
        assert response.json()["keys_affected"] == 2
        assert counts(pool) == {"selected": (8, 0), "other": (9, 0)}


async def test_decrypt_failure_rolls_back_all_claims(pool):
    row = add(pool, "broken")
    row.encrypted_key = "invalid"
    add(pool, "valid")
    with pytest.raises(ValueError, match="invalid stored credential"):
        await key_manager.claim_provider_keys(pool, "gemini")
    assert counts(pool) == {"broken": (0, 0), "valid": (0, 0)}


async def test_endpoint_returns_only_raw_keys_and_no_store(pool):
    add(pool, "one")
    add(pool, "two")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(PATH, headers=ADMIN)
        assert response.status_code == 200
        assert response.json() == ["fake-key-two", "fake-key-one"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert (await client.get(PATH, headers=ADMIN)).status_code == 405
    assert counts(pool) == {"one": (1, 1), "two": (1, 1)}


@pytest.mark.parametrize("headers", [{}, {"X-Cron-Secret": "wrong"}, {"X-API-Key": "ordinary-app-key"}])
async def test_unauthorized_does_not_allocate(pool, headers):
    add(pool, "keep")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(PATH, headers=headers)
    assert response.status_code == 401
    assert "fake-key" not in response.text
    assert counts(pool)["keep"] == (0, 0)


async def test_no_config_empty_and_invalid_provider(pool, monkeypatch):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(PATH, headers=ADMIN)).status_code == 409
        assert (await client.post("/api/v1/keys/provider/bad%20provider/claim", headers=ADMIN)).status_code == 422
        monkeypatch.setattr(settings, "cron_secret", "")
        assert (await client.post(PATH, headers=ADMIN)).status_code == 503


async def test_route_hides_decrypt_details(pool):
    row = add(pool, "broken")
    row.encrypted_key = "private-ciphertext-not-for-response"
    pool.session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(PATH, headers=ADMIN)
    assert response.status_code == 503
    assert response.json() == {"detail": "Provider keys could not be allocated"}
    assert counts(pool)["broken"] == (0, 0)


def test_postgres_claim_locks_selection_and_increments_in_sql():
    sql = str(key_manager._provider_key_claim_statement("gemini").compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE provider_api_keys" in sql and "RETURNING" in sql
    assert "total_count=(provider_api_keys.total_count +" in sql
    assert "requests_today=(provider_api_keys.requests_today +" in sql
    assert "LIMIT" in sql


def test_concurrent_claims_respect_quota_and_keep_all_increments(pool, tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "claim.sqlite"), connect_args={"timeout": 30})
    ProviderAPIKey.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([ProviderAPIKey(provider="gemini", label=str(i), encrypted_key="enc:fake-key-" + str(i), daily_limit=3) for i in range(4)])
        session.commit()

    class DB(AsyncAdapter):
        async def rollback(self):
            self.session.rollback()

    def claim(_):
        with Session(engine) as session:
            return asyncio.run(key_manager.claim_provider_keys(DB(session), "gemini"))

    with ThreadPoolExecutor(max_workers=4) as executor:
        returned = list(executor.map(claim, range(12)))
    with Session(engine) as session:
        rows = session.execute(text("SELECT total_count, requests_today FROM provider_api_keys")).all()
    assert sum(map(len, returned)) == 12
    assert rows == [(3, 3)] * 4
    engine.dispose()


def test_migration_backfill_insert_default_and_downgrade():
    filename = Path(__file__).parents[1] / "alembic/versions/c84f10d2a639_provider_key_total_count.py"
    spec = importlib.util.spec_from_file_location("provider_count_migration", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE provider_api_keys (id INTEGER PRIMARY KEY, label TEXT)"))
        connection.execute(text("INSERT INTO provider_api_keys VALUES (1, 'keep')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(text("SELECT total_count FROM provider_api_keys")).scalar_one() == 0
        connection.execute(text("INSERT INTO provider_api_keys (id, label) VALUES (2, 'new')"))
        assert connection.execute(text("SELECT total_count FROM provider_api_keys WHERE id=2")).scalar_one() == 0
        migration.downgrade()
        assert "total_count" not in {c["name"] for c in inspect(connection).get_columns("provider_api_keys")}
        assert connection.execute(text("SELECT label FROM provider_api_keys ORDER BY id")).scalars().all() == ["keep", "new"]
    engine.dispose()
