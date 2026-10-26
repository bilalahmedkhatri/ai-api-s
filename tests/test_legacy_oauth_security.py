"""Legacy Google login is bound to one browser and consumes state before exchange."""

# ruff: noqa: F811
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_customer_api_keys import customer
from tests.test_dashboard import dashboard  # noqa: F401


async def test_legacy_oauth_wrong_browser_and_replay(dashboard, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "debug", True)
    exchange = AsyncMock(
        return_value={"sub": "existing-google", "email": "admin@gmail.com", "email_verified": True}
    )
    monkeypatch.setattr("app.api.v1.auth.router.exchange_code_for_userinfo", exchange)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/auth/login")
        state = parse_qs(urlparse(first.headers["location"]).query)["state"][0]
        assert "code_challenge=" in first.headers["location"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as foreign:
            assert (
                await foreign.get(
                    "/api/v1/auth/callback", params={"state": state, "code": "test-code"}
                )
            ).status_code == 400
        response = await client.get(
            "/api/v1/auth/callback", params={"state": state, "code": "test-code"}
        )
        assert response.status_code == 302
        assert exchange.await_count == 1 and exchange.call_args.kwargs["code_verifier"]
        assert (
            await client.get("/api/v1/auth/callback", params={"state": state, "code": "test-code"})
        ).status_code == 400
        assert exchange.await_count == 1


async def test_old_cookie_never_revives_after_member_reapproval(dashboard):
    from app.core.google_auth import create_jwt
    from app.models.db_models import User

    db, _, admin = dashboard
    m, _ = await customer(db)
    owner = await db.get(User, m.user_id)
    old_cookie = create_jwt(owner.id, owner.email, session_version=owner.session_version)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("session", old_cookie)
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        assert (
            await client.delete(f"/api/v1/dashboard/members/{m.id}", headers=admin)
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (
            await client.patch(
                f"/api/v1/dashboard/members/{m.id}", headers=admin, json={"status": "approved"}
            )
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        client.cookies.set("session", create_jwt(owner.id, owner.email, owner.session_version))
        assert (await client.get("/api/v1/auth/me")).status_code == 200
