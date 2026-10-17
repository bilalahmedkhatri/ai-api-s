"""Real SQL authorization tests in isolated SQLite; no live DB or Facebook calls."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import settings
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, AuthorizedApp, Base, FacebookPageToken, User
from app.services.api_key_service import generate_api_key, revoke_api_key, verify_api_key
from app.services.app_access_service import normalize_origin
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

ADMIN = {"X-Cron-Secret": "isolated-admin-secret"}
PERMISSIONS = ["GET /api/v1/audio/models", "GET /api/v1/audio/tts/status/{job_id}",
               "POST /api/v1/facebook/store-token", "POST /api/v1/facebook/post-pages"]


class AsyncAdapter:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def add(self, obj):
        self.session.add(obj)

    def get_bind(self):
        return self.session.get_bind()

    async def get(self, model, identifier):
        return self.session.get(model, identifier)

    async def execute(self, stmt):
        return self.session.execute(stmt)

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def flush(self):
        self.session.flush()

    async def refresh(self, obj):
        self.session.refresh(obj)


@pytest.fixture
def isolated(monkeypatch):
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    db = AsyncAdapter(session)
    owner = User(google_id="gateway-owner", email="owner@example.test", is_active=True, api_access_enabled=True)
    session.add(owner)
    session.flush()
    apps = [AuthorizedApp(name=f"App {i}", owner_user_id=owner.id,
                          allowed_origin=f"https://app{i}.example.test",
                          allowed_endpoints_json=json.dumps(PERMISSIONS),
                          is_approved=True, is_active=True) for i in (1, 2)]
    session.add_all(apps)
    session.commit()

    async def fake_db():
        yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = fake_db
    monkeypatch.setattr(settings, "cron_secret", ADMIN["X-Cron-Secret"])
    monkeypatch.setattr("app.core.app_cors.AsyncSessionLocal", lambda: db)
    monkeypatch.setattr("app.api.v1.audio.router.get_dynamic_tts_models", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.api.v1.facebook.router.facebook_client.get_page_info", AsyncMock(return_value={"id": "page", "name": "Test page"}))
    monkeypatch.setattr("app.api.v1.facebook.router.encrypt_token", lambda token: "encrypted:" + token)
    monkeypatch.setattr("app.api.v1.facebook.router.decrypt_token", lambda token: token.removeprefix("encrypted:"))
    publish = AsyncMock(return_value={"id": "test-post"})
    monkeypatch.setattr("app.api.v1.facebook.router.facebook_client.publish_post", publish)
    yield db, owner, apps, publish
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    session.close()
    engine.dispose()


async def headers(db, registered):
    raw, _ = await generate_api_key(db, client_name=registered.name, authorized_app_id=registered.id)
    return {"X-API-Key": raw, "X-App-Origin": registered.allowed_origin}


@pytest.mark.parametrize("origin", [None, "https://foreign.example", "https://sub.app1.example.test",
                                     "http://app1.example.test", "https://app1.example.test:8080",
                                     "null", "https://app1.example.test@evil.test", "*"])
async def test_key_authentication_does_not_require_origin(isolated, origin):
    db, _, apps, _ = isolated
    auth = await headers(db, apps[0])
    assert await verify_api_key(db, auth["X-API-Key"], origin) is not None


async def test_key_generation_revocation_and_unlinked_key(isolated):
    db, _, apps, _ = isolated
    auth = await headers(db, apps[0])
    record = await verify_api_key(db, auth["X-API-Key"], apps[0].allowed_origin)
    assert record and record.last_used_at and record.authorized_app_id == apps[0].id
    assert await verify_api_key(db, "wrong-key", apps[0].allowed_origin) is None
    record.authorized_app_id = None
    await db.commit()
    assert await verify_api_key(db, auth["X-API-Key"], apps[0].allowed_origin) is None
    record.authorized_app_id = apps[0].id
    await db.commit()
    assert await revoke_api_key(db, record.id)
    assert await verify_api_key(db, auth["X-API-Key"], apps[0].allowed_origin) is None


@pytest.mark.parametrize("target,field", [("owner", "is_active"), ("owner", "api_access_enabled"),
                                         ("app", "is_active"), ("app", "is_approved")])
async def test_revocation_takes_effect_on_next_request(isolated, target, field):
    db, owner, apps, _ = isolated
    auth = await headers(db, apps[0])
    setattr(owner if target == "owner" else apps[0], field, False)
    await db.commit()
    assert await verify_api_key(db, auth["X-API-Key"], apps[0].allowed_origin) is None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.get("/api/v1/audio/models", headers=auth)).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/audio/models"), ("GET", "/api/v1/audio/voices?model=test"),
    ("GET", "/api/v1/audio/sample?model=test&voice=test"), ("POST", "/api/v1/audio/tts"),
    ("GET", "/api/v1/audio/tts/status/job"), ("POST", "/api/v1/audio/tts/renew-url"),
    ("POST", "/api/v1/query/"),
    ("POST", "/api/v1/ingest/image/"), ("POST", "/api/v1/ingest/video/"),
    ("GET", "/api/v1/image/models"), ("POST", "/api/v1/image/generate"),
    ("GET", "/api/v1/video/models"), ("POST", "/api/v1/video/generate"),
    ("POST", "/api/v1/facebook/store-token"), ("POST", "/api/v1/facebook/post-pages"),
    ("POST", "/api/v1/media/process-urls"), ("GET", "/api/v1/media/item?user_id=external"),
    ("DELETE", "/api/v1/media/"),
])
async def test_all_app_routes_require_authorization(isolated, monkeypatch, method, path):
    monkeypatch.setattr(settings, "require_api_key_for_query", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        response = await client.request(method, path, headers={"Origin": "https://foreign.example"})
        assert response.status_code == 401, (path, response.text)


async def test_allowed_endpoint_and_bearer_and_referer(isolated):
    db, _, apps, _ = isolated
    auth = await headers(db, apps[0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.get("/api/v1/audio/models", headers=auth)).status_code == 200
        assert (await client.get("/api/v1/audio/tts/status/missing", headers=auth)).status_code == 404
        assert (await client.get("/api/v1/image/models", headers=auth)).status_code == 403
        assert (await client.get("/api/v1/audio/models", headers={"Authorization": "Bearer " + auth["X-API-Key"], "Referer": apps[0].allowed_origin + "/studio"})).status_code == 200
        assert (await client.get("/api/v1/audio/models", headers={**auth, "Origin": apps[1].allowed_origin})).status_code == 200
        assert (await client.get("/api/v1/audio/models?api_key=" + auth["X-API-Key"], headers={"X-App-Origin": apps[0].allowed_origin})).status_code == 401


async def test_facebook_same_page_isolated_per_app(isolated):
    db, owner, apps, publish = isolated
    auth1, auth2 = await headers(db, apps[0]), await headers(db, apps[1])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.post("/api/v1/facebook/store-token", headers=auth1,
                                  json={"page_id": "page", "access_token": "first-secret"})).status_code == 200
        response = await client.post("/api/v1/facebook/post-pages", headers=auth2, data={"page_id": "page", "message": "test"})
        assert response.status_code == 404
        publish.assert_not_awaited()
        assert (await client.post("/api/v1/facebook/store-token", headers=auth2,
                                  json={"page_id": "page", "access_token": "second-secret"})).status_code == 200
        rows = (await db.execute(select(FacebookPageToken))).scalars().all()
        assert len(rows) == 2 and all(row.user_id == owner.id for row in rows)
        assert {row.authorized_app_id for row in rows} == {a.id for a in apps}
        for auth, secret in [(auth1, "first-secret"), (auth2, "second-secret")]:
            response = await client.post("/api/v1/facebook/post-pages", headers=auth, data={"page_id": "page", "message": "test"})
            assert response.status_code == 200
            assert publish.await_args.kwargs["access_token"] == secret


async def test_legacy_facebook_token_not_accessible(isolated):
    db, owner, apps, publish = isolated
    db.add(FacebookPageToken(user_id=owner.id, page_id="legacy", encrypted_token="legacy-secret"))
    await db.commit()
    auth = await headers(db, apps[0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.post("/api/v1/facebook/post-pages", headers=auth, data={"page_id": "legacy", "message": "test"})).status_code == 404
    publish.assert_not_awaited()


async def test_facebook_reconnect_updates_only_current_app(isolated):
    db, _, apps, _ = isolated
    auth1, auth2 = await headers(db, apps[0]), await headers(db, apps[1])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        for auth, token in [(auth1, "first"), (auth2, "second"), (auth1, "renewed")]:
            assert (await client.post("/api/v1/facebook/store-token", headers=auth, json={"page_id": "page", "access_token": token})).status_code == 200
    rows = (await db.execute(select(FacebookPageToken).order_by(FacebookPageToken.authorized_app_id))).scalars().all()
    assert [row.encrypted_token for row in rows] == ["encrypted:renewed", "encrypted:second"]


async def test_facebook_disconnect_is_app_scoped(isolated):
    db, _, apps, publish = isolated
    for registered in apps:
        registered.allowed_endpoints_json = json.dumps(PERMISSIONS + ["DELETE /api/v1/facebook/pages/{page_id}"])
    await db.commit()
    auth1, auth2 = await headers(db, apps[0]), await headers(db, apps[1])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        await client.post("/api/v1/facebook/store-token", headers=auth1, json={"page_id": "page", "access_token": "secret"})
        assert (await client.delete("/api/v1/facebook/pages/page", headers=auth2)).status_code == 404
        assert (await client.delete("/api/v1/facebook/pages/page", headers=auth1)).status_code == 200
        assert (await client.post("/api/v1/facebook/post-pages", headers=auth1, data={"page_id": "page", "message": "test"})).status_code == 404
    publish.assert_not_awaited()


async def test_expired_facebook_token_requires_reconnect(isolated):
    db, _, apps, publish = isolated
    auth = await headers(db, apps[0])
    response = httpx.Response(400, json={"error": {"code": 190, "message": "Expired"}}, request=httpx.Request("POST", "https://graph.example.test"))
    publish.side_effect = httpx.HTTPStatusError("Expired token", request=response.request, response=response)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        await client.post("/api/v1/facebook/store-token", headers=auth, json={"page_id": "page", "access_token": "secret"})
        assert (await client.post("/api/v1/facebook/post-pages", headers=auth, data={"page_id": "page", "message": "test"})).status_code == 401
    record = (await db.execute(select(FacebookPageToken))).scalar_one()
    assert record.is_active is False


async def test_app_admin_management_and_key_creation(isolated):
    db, owner, apps, _ = isolated
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.get("/api/v1/apps/")).status_code == 401
        assert (await client.get("/api/v1/apps/endpoints", headers=ADMIN)).status_code == 200
        created = await client.post("/api/v1/apps/", headers=ADMIN, json={
            "name": "New app", "owner_user_id": owner.id, "allowed_origin": "HTTPS://NEW.EXAMPLE:443/",
            "allowed_endpoints": PERMISSIONS, "is_approved": True,
        })
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]
        assert created.json()["allowed_origin"] == "https://new.example"
        key = await client.post("/api/v1/keys/", headers=ADMIN, json={"client_name": "New app", "authorized_app_id": app_id})
        assert key.status_code == 201, key.text
        assert key.json()["authorized_app_id"] == app_id and key.json()["raw_key"].startswith("gw_live_")
        assert (await client.get("/api/v1/keys/", headers=ADMIN)).status_code == 200
        assert (await client.delete(f'/api/v1/keys/{key.json()["id"]}', headers=ADMIN)).status_code == 200
        assert (await client.patch(f"/api/v1/apps/{app_id}", headers=ADMIN, json={"is_active": False})).json()["is_active"] is False
        assert (await client.patch(f"/api/v1/apps/owners/{owner.id}/permission", headers=ADMIN,
                                  json={"api_access_enabled": False})).status_code == 200
        assert (await client.post("/api/v1/keys/", headers=ADMIN, json={"client_name": "Denied app", "authorized_app_id": apps[0].id})).status_code == 400


async def test_legacy_key_explicit_link_and_transfer_denied(isolated):
    db, _, apps, _ = isolated
    _, key = await generate_api_key(db, "Legacy", apps[0].id)
    key.authorized_app_id = None
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.post(f"/api/v1/apps/{apps[0].id}/link-key/{key.id}", headers=ADMIN)).status_code == 200
        assert (await client.post(f"/api/v1/apps/{apps[1].id}/link-key/{key.id}", headers=ADMIN)).status_code == 409


async def test_admin_fails_closed_without_secret(isolated, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        assert (await client.get("/api/v1/apps/")).status_code == 503
        assert (await client.get("/api/v1/keys/")).status_code == 503


async def test_dynamic_cors_from_registered_apps(isolated):
    db, owner, apps, _ = isolated
    auth = await headers(db, apps[0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway.test") as client:
        preflight = {"Origin": apps[0].allowed_origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "X-API-Key"}
        accepted = await client.options("/api/v1/audio/models", headers=preflight)
        assert accepted.status_code == 204
        assert accepted.headers["access-control-allow-origin"] == apps[0].allowed_origin
        response = await client.get("/api/v1/audio/models", headers={**auth, "Origin": apps[0].allowed_origin})
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == apps[0].allowed_origin
        assert (await client.options("/api/v1/audio/models", headers={**preflight, "Origin": "https://foreign.example"})).status_code == 403
        owner.api_access_enabled = False
        await db.commit()
        assert (await client.options("/api/v1/audio/models", headers=preflight)).status_code == 403


@pytest.mark.parametrize("value", ["example.com", "*", "https://*.example.com", "https://user:secret@example.com", "https://example.com/path", "https://example.com?query=1", "https://example.com:invalid", "https://example.com\\evil"])
def test_invalid_registration_origins(value):
    with pytest.raises(ValueError):
        normalize_origin(value)


def test_origin_canonicalization():
    assert normalize_origin("HTTPS://EXAMPLE.COM:443/") == "https://example.com"
    assert normalize_origin("http://localhost:8000") == "http://localhost:8000"
