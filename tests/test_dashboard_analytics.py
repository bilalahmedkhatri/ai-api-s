"""Analytics use isolated SQL fixtures, never configured gateway data."""

from datetime import timedelta

import pytest
from app.models.dashboard import AuditLog, Permission
from app.models.db_models import FinalResponse
from app.services.dashboard import now
from httpx import ASGITransport, AsyncClient
from main import app

pytest_plugins = ["tests.test_dashboard"]


@pytest.mark.asyncio
async def test_analytics_totals_missing_metrics_and_member_scope(dashboard):
    db, m, headers = dashboard
    for actor, status, elapsed in [(m.id, 200, 100), (m.id, 500, 300), (None, 200, None)]:
        db.add(
            AuditLog(
                actor_membership_id=actor,
                action="POST",
                target="/api/v1/query/",
                status=status,
                elapsed_ms=elapsed,
                created_at=now(),
            )
        )
    db.add(
        AuditLog(
            actor_membership_id=m.id,
            action="SIGN_IN",
            target="dashboard",
            status=200,
            created_at=now(),
        )
    )
    db.add(
        AuditLog(
            actor_membership_id=m.id,
            action="GET",
            target="/health",
            status=200,
            elapsed_ms=900,
            created_at=now() - timedelta(days=100),
        )
    )
    db.add(
        FinalResponse(
            query_id=1,
            generated_output="isolated",
            model_used="isolated-model",
            prompt_tokens=20,
            completion_tokens=30,
            cost_usd=0.02,
            created_at=now(),
        )
    )
    db.add(
        FinalResponse(
            query_id=2, generated_output="isolated", model_used="isolated-model", created_at=now()
        )
    )
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/analytics?days=7", headers=headers)
        assert response.status_code == 200
        d = response.json()
        assert d["requests"] == 3 and d["errors"] == 1
        assert d["success_rate"] == pytest.approx(200 / 3)
        assert d["latency_ms"] == 200 and d["latency_samples"] == 2
        assert d["usage"]["prompt_tokens"] == 20 and d["usage"]["completion_tokens"] == 30
        assert d["usage"]["cost_usd"] == 0.02 and d["usage"]["cost_samples"] == 1
        assert d["usage"]["query_responses"] == 2 and len(d["series"]) == 7
        assert all(p["latency_ms"] is None for p in d["series"][:-1])
        assert "isolated-model" not in response.text and "generated_output" not in response.text
        assert (
            await client.get("/api/v1/dashboard/analytics?days=8", headers=headers)
        ).status_code == 400
        assert (await client.get("/api/v1/dashboard/analytics")).status_code == 401
        m.role = "operator"
        db.add(
            Permission(
                membership_id=m.id, resource_type="page", resource_key="overview", action="view"
            )
        )
        await db.commit()
        d = (await client.get("/api/v1/dashboard/analytics", headers=headers)).json()
        assert (
            d["requests"] == 2 and d["usage"] is None and d["scope"] == "Your dashboard API calls"
        )


@pytest.mark.asyncio
async def test_analytics_empty_is_not_measured_zero(dashboard):
    db, m, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        d = (await client.get("/api/v1/dashboard/analytics?days=30", headers=headers)).json()
        assert d["requests"] == 0 and d["success_rate"] is None and d["latency_ms"] is None
        assert d["usage"]["cost_usd"] is None and len(d["series"]) == 30
        m.role = "viewer"
        await db.commit()
        assert (await client.get("/api/v1/dashboard/analytics", headers=headers)).status_code == 403


def test_analytics_migration_roundtrip():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    file = (
        Path(__file__).resolve().parents[1] / "alembic/versions/f73a9d20bc41_dashboard_analytics.py"
    )
    spec = importlib.util.spec_from_file_location("analytics_migration", file)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE audit_logs (id INTEGER PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(c))
        migration.upgrade()
        assert "elapsed_ms" in {col["name"] for col in inspect(c).get_columns("audit_logs")}
        migration.downgrade()
        assert "elapsed_ms" not in {col["name"] for col in inspect(c).get_columns("audit_logs")}
        migration.upgrade()
    engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_day_bucket_uses_same_parameter_in_group_by():
    from types import SimpleNamespace

    from app.services.analytics import dashboard_analytics
    from sqlalchemy.dialects.postgresql.asyncpg import dialect

    class CaptureDB:
        def get_bind(self):
            return SimpleNamespace(dialect=dialect())

        async def execute(self, statement):
            compiled = statement.compile(dialect=dialect())
            # PostgreSQL treats separate bind positions as different expressions.
            # SELECT and GROUP BY must reuse the exact UTC bucket expression.
            assert list(compiled.params.values()).count("UTC") == 1
            return SimpleNamespace(all=lambda: [])

    result = await dashboard_analytics(CaptureDB(), SimpleNamespace(role="admin"), 7)
    assert len(result["series"]) == 7
