# ruff: noqa: F811
"""Customer activation, one-time issuance and tenant isolation on real isolated SQL."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from app.models.dashboard import (
    AccessRequest,
    AuditLog,
    DashboardSession,
    Membership,
    ServiceCredential,
)
from app.models.db_models import AccessAPIKey, AuthorizedApp, TTSJob, User
from app.services.api_key_service import customer_project_active, hash_key
from app.services.dashboard import digest, now
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

from tests.test_dashboard import dashboard  # noqa: F401, F811

PATH = "/api/v1/dashboard/api-keys"


@pytest.fixture(autouse=True)
def reset_key_rate_limits():
    from app.core.middleware import limiter
    limiter.reset()
    yield
    limiter.reset()


async def customer(db, email="customer@gmail.com"):
    owner = User(google_id=email, email=email, is_active=True, api_access_enabled=False)
    db.add(owner)
    await db.flush()
    m = Membership(email=email, user_id=owner.id, name="Customer", role="viewer",
                   status="approved", created_at=now())
    db.add(m)
    await db.flush()
    db.add(DashboardSession(token_hash=digest(email), membership_id=m.id,
                           created_at=now(), expires_at=now() + timedelta(hours=1)))
    await db.commit()
    return m, {"Origin": "http://localhost:3002", "Authorization": "Bearer " + email}


async def enable(client, m, admin):
    assert (await client.post(f"/api/v1/dashboard/members/{m.id}/api-access", headers=admin)).status_code == 200
    members = await client.get("/api/v1/dashboard/members", headers=admin)
    assert members.headers["cache-control"] == "no-store"
    return next(row["master_key"] for row in members.json()["users"] if row["id"] == m.id)


async def issue(client, headers, master):
    response = await client.post(PATH, headers=headers, json={"name": "My app", "master_key": master})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_first_activation_and_one_time_secret(dashboard, monkeypatch):
    db, _, admin = dashboard
    m, headers = await customer(db)
    monkeypatch.setattr("app.api.v1.audio.router.get_dynamic_tts_models", AsyncMock(return_value=[]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(PATH)).status_code == 401
        assert (await client.post(PATH, headers=headers, json={"name": "My app"})).status_code == 403
        master = await enable(client, m, admin)
        assert master.startswith("gw_master_") and master not in m.master_key_encrypted
        assert (await enable(client, m, admin)) == master  # Does not rotate on repeated approval.
        for value in (None, "wrong-master"):
            denied = await client.post(PATH, headers=headers, json={"name": "My app", "master_key": value})
            assert denied.status_code == 403 and not m.api_keys_unlocked
        before = (await client.get(PATH, headers=headers)).json()
        assert before == {"enabled": True, "unlocked": False, "keys": []}
        first = await issue(client, headers, master)
        assert m.api_keys_unlocked
        record = await db.get(AccessAPIKey, first["id"])
        assert record.hashed_key == hash_key(first["secret"])
        assert record.hashed_key != first["secret"]
        assert (await db.execute(select(ServiceCredential))).scalars().all() == []
        # Master key is not required again, even across fresh session requests.
        second = await issue(client, headers, None)
        assert first["secret"] != second["secret"]
        assert record.authorized_app_id == (await db.get(AccessAPIKey, second["id"])).authorized_app_id
        metadata = await client.get(PATH, headers=headers)
        assert metadata.headers["cache-control"] == "no-store"
        assert metadata.json()["unlocked"] and len(metadata.json()["keys"]) == 2
        me = await client.get("/api/v1/dashboard/me", headers=headers)
        for response in (metadata, me):
            assert first["secret"] not in response.text and master not in response.text
        assert (await client.get("/api/v1/dashboard/members", headers=headers)).status_code == 403
        for origin in (None, "https://unregistered.test", "null"):
            auth = {"Authorization": "Bearer " + first["secret"]}
            if origin:
                auth["Origin"] = origin
            assert (await client.get("/api/v1/audio/models", headers=auth)).status_code == 200
        assert (await client.get("/api/v1/audio/models", headers={"X-API-Key": first["secret"]})).status_code == 200
        assert (await client.get("/api/v1/audio/models", headers={"X-API-Key": master})).status_code == 401
        assert (await client.get("/api/v1/keys/", headers={"Authorization": "Bearer " + first["secret"]})).status_code == 401
        assert (await client.get("/api/v1/dashboard/members", headers={"Authorization": "Bearer " + first["secret"]})).status_code == 401
        logs = (await db.execute(select(AuditLog))).scalars().all()
        assert all(master not in row.target and first["secret"] not in row.target for row in logs)


async def test_scoped_revoke_and_job_ownership(dashboard):
    db, _, admin = dashboard
    a, ah = await customer(db, "a@gmail.com")
    b, bh = await customer(db, "b@gmail.com")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ak = await issue(client, ah, await enable(client, a, admin))
        bk = await issue(client, bh, await enable(client, b, admin))
        assert (await client.get(PATH, headers=bh)).json()["keys"][0]["id"] == bk["id"]
        assert (await client.delete(f"{PATH}/{ak['id']}", headers=bh)).status_code == 404
        db.add(TTSJob(job_id="owned-job", text="Private text", status="completed",
                      authorized_app_id=a.api_project_id))
        await db.commit()
        assert (await client.get("/api/v1/audio/tts/status/owned-job", headers={"X-API-Key": ak["secret"]})).status_code == 200
        assert (await client.get("/api/v1/audio/tts/status/owned-job", headers={"X-API-Key": bk["secret"]})).status_code == 404
        assert (await client.delete(f"{PATH}/{ak['id']}", headers=ah)).status_code == 200
        assert (await client.get("/api/v1/audio/models", headers={"X-API-Key": ak["secret"]})).status_code == 401
        replacement = await issue(client, ah, None)
        assert (await client.get("/api/v1/audio/tts/status/owned-job", headers={"X-API-Key": replacement["secret"]})).status_code == 200


@pytest.mark.parametrize("state", ["revoked", "pending", "expired", "inactive-owner", "disabled-api", "disabled-project"])
async def test_account_and_project_revocation(dashboard, state):
    db, _, admin = dashboard
    m, headers = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        key = await issue(client, headers, await enable(client, m, admin))
        if state in {"pending", "revoked"}:
            m.status = state
        elif state == "expired":
            m.access_expires_at = now() - timedelta(seconds=1)
        elif state == "disabled-project":
            (await db.get(AuthorizedApp, m.api_project_id)).is_active = False
        else:
            setattr(await db.get(User, m.user_id), "is_active" if state == "inactive-owner" else "api_access_enabled", False)
        await db.commit()
        assert (await client.get("/api/v1/audio/models", headers={"X-API-Key": key["secret"]})).status_code == 401
        if state in {"pending", "revoked", "expired"}:
            assert not await customer_project_active(db, m.api_project_id)


async def test_master_validation_csrf_and_no_secret_echo(dashboard):
    db, _, admin = dashboard
    m, headers = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        master = await enable(client, m, admin)
        assert (await client.post(f"/api/v1/dashboard/members/{m.id}/api-access", headers=headers)).status_code == 403
        for origin in ("https://evil.test", "null", ""):
            response = await client.post(PATH, headers={**headers, "Origin": origin}, json={"name": "My app", "master_key": master})
            assert response.status_code == 403
        response = await client.post(PATH, headers=headers, json={"name": "", "master_key": master})
        assert response.status_code == 422 and master not in response.text
        response = await client.post(PATH, headers=headers, json={"name": "My app", "master_key": {"secret": master}})
        assert response.status_code == 422 and master not in response.text
        assert not m.api_keys_unlocked and m.api_project_id is None


async def test_approval_creates_master_before_google_login(dashboard):
    db, _, admin = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/dashboard/members", headers=admin, json={
            "email": "new@gmail.com", "name": "New", "status": "approved", "role": "viewer", "permissions": [],
        })
        assert response.status_code == 200
        row = (await db.execute(select(Membership).where(Membership.email == "new@gmail.com"))).scalar_one()
        assert row.master_key_encrypted and row.user_id is None
        assert "master_key" not in response.json()
        assert (await client.get("/api/v1/dashboard/members", headers=admin)).json()["users"][0]["master_key"].startswith("gw_master_")


async def test_master_guessing_is_rate_limited(dashboard):
    db, _, admin = dashboard
    m, headers = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await enable(client, m, admin)
        for _ in range(10):
            assert (await client.post(PATH, headers=headers, json={"name": "My app", "master_key": "wrong"})).status_code == 403
        assert (await client.post(PATH, headers=headers, json={"name": "My app", "master_key": "wrong"})).status_code == 429
        assert not m.api_keys_unlocked


async def test_existing_admin_generates_without_master_key(dashboard, monkeypatch):
    from app.core.config import settings
    db, admin, headers = dashboard
    assert admin.master_key_encrypted is None and not admin.api_keys_unlocked
    # Admin issuance hashes the new API secret and needs no customer vault gate.
    monkeypatch.setattr(settings, "encryption_key", "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state = (await client.get(PATH, headers=headers)).json()
        assert state["enabled"] and state["unlocked"]
        key = await issue(client, headers, None)
        assert key["secret"].startswith("gw_live_")
        assert admin.api_keys_unlocked and admin.master_key_encrypted is None
        assert (await db.get(AccessAPIKey, key["id"])).authorized_app_id == admin.api_project_id


@pytest.mark.parametrize("verified", [True, False])
async def test_review_pending_request_for_existing_membership(dashboard, verified):
    db, _, headers = dashboard
    target, _ = await customer(db)
    target.status = "pending"
    entry = AccessRequest(email=target.email, name=target.name, reason="Need API access",
                          status="pending", email_verified=verified, created_at=now())
    db.add(entry)
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = (await client.get("/api/v1/dashboard/members", headers=headers)).json()
        assert any(r["email"] == target.email and r["status"] == "pending" for r in listing["requests"])
        result = await client.patch(f"/api/v1/dashboard/members/{target.id}", headers=headers,
                                    json={"role": "viewer", "status": "approved", "permissions": []})
        assert result.status_code == (200 if verified else 400)
        assert target.status == entry.status == ("approved" if verified else "pending")
        assert bool(target.master_key_encrypted) == verified
