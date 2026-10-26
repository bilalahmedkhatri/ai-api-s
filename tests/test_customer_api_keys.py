"""Real isolated SQL: automatic setup, shared CRUD policy and permanent revocation."""

# ruff: noqa: F811
import base64
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.models.dashboard import (
    AccessRequest,
    AuditLog,
    DashboardSession,
    Membership,
    Permission,
    ServiceCredential,
)
from app.models.db_models import AccessAPIKey, AuthorizedApp, TTSJob, User
from app.services.api_key_service import generate_api_key, hash_key, verify_api_key
from app.services.customer_keys import execution_credential
from app.services.dashboard import digest, now
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

from tests.test_dashboard import dashboard  # noqa: F401

PATH = "/api/v1/dashboard/api-keys"
READ = "GET /api/v1/audio/models"
CREATE = "POST /api/v1/generate/"


@pytest.fixture(autouse=True)
def reset_key_rate_limits():
    from app.core.middleware import limiter

    limiter.reset()
    yield
    limiter.reset()


async def customer(db, email="customer@gmail.com", role="viewer", grants=(READ,)):
    owner = User(google_id=email, email=email, is_active=True, api_access_enabled=True)
    db.add(owner)
    await db.flush()
    m = Membership(
        email=email,
        user_id=owner.id,
        name="Customer",
        role=role,
        status="approved",
        created_at=now(),
    )
    db.add(m)
    await db.flush()
    db.add(
        DashboardSession(
            token_hash=digest(email),
            membership_id=m.id,
            created_at=now(),
            expires_at=now() + timedelta(hours=1),
        )
    )
    for grant in grants:
        db.add(
            Permission(
                membership_id=m.id, resource_type="endpoint", resource_key=grant, action="execute"
            )
        )
    await db.commit()
    return m, {"Origin": "http://localhost:3002", "Authorization": "Bearer " + email}


async def issue(client, headers, master=None):
    response = await client.post(
        PATH, headers=headers, json={"name": "My app", "master_key": master}
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_automatic_setup_and_one_time_secret(dashboard, monkeypatch):
    db, _, admin = dashboard
    m, headers = await customer(db)
    monkeypatch.setattr(
        "app.api.v1.audio.router.get_dynamic_tts_models", AsyncMock(return_value=[])
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(PATH)).status_code == 401
        first = await issue(client, headers)
        second = await issue(client, headers, "ignored-legacy-input")
        assert first["secret"] != second["secret"]
        record = await db.get(AccessAPIKey, first["id"])
        assert record.hashed_key == hash_key(first["secret"])
        project = await db.get(AuthorizedApp, m.api_project_id)
        assert json.loads(project.allowed_endpoints_json) == [READ]
        assert m.master_key_encrypted is None
        assert (
            await client.get("/api/v1/audio/models", headers={"X-API-Key": first["secret"]})
        ).status_code == 200
        last_used = record.last_used_at
        assert (
            await client.post(
                "/api/v1/generate/",
                headers={"X-API-Key": first["secret"]},
                json={"prompt": "forbidden"},
            )
        ).status_code == 403
        assert record.last_used_at == last_used
        for endpoint in (PATH, "/api/v1/dashboard/me", "/api/v1/dashboard/members"):
            response = await client.get(
                endpoint, headers=admin if endpoint.endswith("members") else headers
            )
            assert (
                first["secret"] not in response.text and "ignored-legacy-input" not in response.text
            )


@pytest.mark.parametrize(
    "role,write_grant,components,allowed",
    [
        ("viewer", True, True, False),
        ("operator", False, True, False),
        ("operator", True, False, False),
        ("operator", True, True, True),
        ("admin", False, False, True),
    ],
)
async def test_shared_crud_policy(dashboard, role, write_grant, components, allowed):
    from app.services.access_policy import member_operation_allowed

    db, _, _ = dashboard
    m, _ = await customer(db, role=role, grants=(CREATE,) if write_grant else ())
    if components:
        for key in ("endpoint.execute", "endpoint.create"):
            db.add(
                Permission(
                    membership_id=m.id, resource_type="component", resource_key=key, action="view"
                )
            )
    await db.commit()
    assert await member_operation_allowed(db, m, "POST", "/api/v1/generate/") is allowed
    project = AuthorizedApp(
        owner_user_id=m.user_id,
        name="Legacy",
        allowed_origin="",
        is_active=True,
        is_approved=True,
        allowed_endpoints_json=json.dumps([CREATE]),
    )
    db.add(project)
    await db.commit()
    raw, _ = await generate_api_key(db, "Legacy", project.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Invalid request body yields 422 only after the real auth dependency allows it.
        response = await client.post("/api/v1/generate/", headers={"X-API-Key": raw}, json={})
        assert response.status_code == (422 if allowed else 403), response.text


async def test_generation_get_is_not_a_viewer_read(dashboard, monkeypatch):
    db, _, _ = dashboard
    sample = "GET /api/v1/audio/sample"
    m, _ = await customer(db, grants=(sample,))
    project = AuthorizedApp(
        owner_user_id=m.user_id,
        name="Legacy",
        allowed_origin="",
        is_active=True,
        is_approved=True,
        allowed_endpoints_json=json.dumps([sample]),
    )
    db.add(project)
    await db.commit()
    raw, _ = await generate_api_key(db, "Legacy", project.id)
    generate = AsyncMock()
    monkeypatch.setattr("app.api.v1.audio.router.generate_dynamic_speech", generate)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/audio/sample",
            params={"model": "test", "voice": "test"},
            headers={"X-API-Key": raw},
        )
        assert response.status_code == 403
    generate.assert_not_awaited()


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_ordinary_user_cannot_manage_another_member(dashboard, method):
    db, _, _ = dashboard
    _, ah = await customer(db, "a@gmail.com")
    other, _ = await customer(db, "b@gmail.com")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method,
            f"/api/v1/dashboard/members/{other.id}",
            headers=ah,
            **({"json": {"status": "revoked"}} if method == "PATCH" else {}),
        )
        assert response.status_code == 403 and other.status == "approved"


async def test_scoped_revoke_and_job_ownership(dashboard):
    db, _, _ = dashboard
    grant = "GET /api/v1/audio/tts/status/{job_id}"
    a, ah = await customer(db, "a@gmail.com", grants=(grant,))
    b, bh = await customer(db, "b@gmail.com", grants=(grant,))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ak, bk = await issue(client, ah), await issue(client, bh)
        assert (await client.delete(f"{PATH}/{ak['id']}", headers=bh)).status_code == 404
        db.add(
            TTSJob(
                job_id="owned-job",
                text="Private",
                status="completed",
                authorized_app_id=a.api_project_id,
            )
        )
        await db.commit()
        for key, status in ((ak, 200), (bk, 404)):
            response = await client.get(
                "/api/v1/audio/tts/status/owned-job", headers={"X-API-Key": key["secret"]}
            )
            assert response.status_code == status
        assert (await client.delete(f"{PATH}/{ak['id']}", headers=ah)).status_code == 200
        assert await verify_api_key(db, ak["secret"]) is None


async def test_revoke_reapprove_never_revives_keys_in_any_owned_app(dashboard):
    db, _, admin = dashboard
    m, headers = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        key = await issue(client, headers)
        extra = AuthorizedApp(
            owner_user_id=m.user_id,
            name="Legacy",
            allowed_origin="",
            is_active=True,
            is_approved=True,
            allowed_endpoints_json=json.dumps([READ]),
        )
        db.add(extra)
        await db.commit()
        raw, old = await generate_api_key(db, "Legacy", extra.id)
        assert (
            await client.delete(f"/api/v1/dashboard/members/{m.id}", headers=admin)
        ).status_code == 200
        assert not (await db.get(AccessAPIKey, old.id)).is_active
        assert await verify_api_key(db, raw) is None
        assert (
            await client.patch(
                f"/api/v1/dashboard/members/{m.id}", headers=admin, json={"status": "approved"}
            )
        ).status_code == 200
        assert await verify_api_key(db, key["secret"]) is None
        assert await verify_api_key(db, raw) is None
        assert (await client.get(PATH, headers=headers)).status_code == 401


@pytest.mark.parametrize(
    "state", ["revoked", "pending", "expired", "inactive-owner", "disabled-api", "disabled-project"]
)
async def test_account_and_project_revocation(dashboard, state):
    db, _, _ = dashboard
    m, headers = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        key = await issue(client, headers)
        if state in {"revoked", "pending"}:
            m.status = state
        elif state == "expired":
            m.access_expires_at = now() - timedelta(seconds=1)
        elif state == "disabled-project":
            (await db.get(AuthorizedApp, m.api_project_id)).is_active = False
        else:
            setattr(
                await db.get(User, m.user_id),
                "is_active" if state == "inactive-owner" else "api_access_enabled",
                False,
            )
        await db.commit()
        assert (
            await client.get("/api/v1/audio/models", headers={"X-API-Key": key["secret"]})
        ).status_code == 401


async def test_disabled_owner_not_implicitly_enabled_and_secrets_not_echoed(dashboard, caplog):
    db, _, admin = dashboard
    m, headers = await customer(db)
    owner = await db.get(User, m.user_id)
    owner.api_access_enabled = False
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(PATH, headers=headers, json={"name": "Valid"})).status_code == 403
        assert not owner.api_access_enabled and m.api_project_id is None
        marker = "audit-secret-marker-never-echo"
        for path, body in (
            (PATH, {"name": "", "master_key": marker}),
            ("/api/v1/dashboard/credentials", {"label": "x", "kind": "invalid", "secret": marker}),
            ("/api/v1/keys/provider", {"plain_key": marker}),
        ):
            response = await client.post(path, headers=admin, json=body)
            assert marker not in response.text
        assert marker not in caplog.text
        for origin in ("https://evil.test", "null", ""):
            assert (
                await client.post(
                    PATH, headers={**headers, "Origin": origin}, json={"name": "Valid"}
                )
            ).status_code == 403


async def test_managed_credentials_are_bound_reused_and_hidden(dashboard):
    db, _, admin = dashboard
    m, headers = await customer(db)
    row, raw = await execution_credential(db, m)
    await db.commit()
    same, again = await execution_credential(db, m)
    await db.commit()
    assert row.id == same.id and raw == again
    assert row.authorized_app_id == m.api_project_id and row.membership_id == m.id
    key = await db.get(AccessAPIKey, row.access_api_key_id)
    assert key.is_internal and key.hashed_key == hash_key(raw)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path, auth in (
            (PATH, headers),
            ("/api/v1/dashboard/credentials", admin),
            ("/api/v1/dashboard/operation-credentials", headers),
        ):
            response = await client.get(path, headers=auth)
            assert (
                response.status_code == 200
                and raw not in response.text
                and row.label not in response.text
            )
        assert (await client.delete(f"{PATH}/{key.id}", headers=headers)).status_code == 404
        assert (
            await client.delete(f"/api/v1/dashboard/credentials/{row.id}", headers=admin)
        ).status_code == 409


async def test_patch_omission_identity_and_explicit_clear(dashboard):
    db, _, admin = dashboard
    m, _ = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/dashboard/members/{m.id}"
        assert (await client.patch(url, headers=admin, json={"name": "Updated"})).status_code == 200
        detail = await client.get(url, headers=admin)
        assert READ in json.loads(detail.json()["grants"]) and detail.json()["name"] == "Updated"
        assert (
            await client.patch(url, headers=admin, json={"email": "another@gmail.com"})
        ).status_code == 400
        assert (await client.patch(url, headers=admin, json={"permissions": []})).status_code == 200
        assert json.loads((await client.get(url, headers=admin)).json()["grants"]) == []


@pytest.mark.parametrize("verified", [True, False])
async def test_pending_approval_still_requires_verified_request(dashboard, verified):
    db, _, admin = dashboard
    m, _ = await customer(db)
    m.status = "pending"
    db.add(
        AccessRequest(
            email=m.email,
            name=m.name,
            reason="Need API access",
            status="pending",
            email_verified=verified,
            created_at=now(),
        )
    )
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.patch(
            f"/api/v1/dashboard/members/{m.id}", headers=admin, json={"status": "approved"}
        )
        assert result.status_code == (200 if verified else 400)
        assert m.status == ("approved" if verified else "pending")


async def test_readonly_impact_report_never_provisions_or_decrypts(dashboard):
    from types import SimpleNamespace

    from scripts.audit_user_access import report

    db, _, _ = dashboard
    m, _ = await customer(db, grants=())
    m.master_key_encrypted = "must-not-be-decrypted"
    project = AuthorizedApp(
        owner_user_id=m.user_id,
        name="Legacy",
        allowed_origin="",
        is_active=True,
        is_approved=True,
        allowed_endpoints_json=json.dumps([READ]),
    )
    db.add(project)
    await db.commit()
    db.connection = AsyncMock(
        return_value=SimpleNamespace(
            run_sync=AsyncMock(side_effect=lambda fn: fn(db.session.connection()))
        )
    )
    output = await report(db)
    row = next(r for r in output["members"] if r["membership_id"] == m.id)
    assert row["missing_explicit_grants"] and row["projects"][0]["removed_routes"] == [READ]
    assert m.api_project_id is None and output["writes_performed"] is False
    assert m.email not in json.dumps(output) and m.master_key_encrypted not in json.dumps(output)


async def test_approval_has_no_master_secret_and_creates_no_unverified_owner(dashboard):
    db, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/dashboard/members",
            headers=headers,
            json={"email": "new@gmail.com", "name": "New", "status": "approved", "role": "viewer"},
        )
        assert response.status_code == 200
        row = (
            await db.execute(select(Membership).where(Membership.email == "new@gmail.com"))
        ).scalar_one()
        assert (
            row.user_id is None and row.master_key_encrypted is None and row.api_project_id is None
        )
        duplicate = await client.post(
            "/api/v1/dashboard/members", headers=headers, json={"email": "NEW@gmail.com"}
        )
        assert duplicate.status_code == 409


async def test_key_creation_is_rate_limited(dashboard):
    db, _, _ = dashboard
    _, headers = await customer(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(10):
            await issue(client, headers)
        assert (await client.post(PATH, headers=headers, json={"name": "More"})).status_code == 429


async def test_secret_operation_excluded_and_foreign_credential_denied(dashboard):
    db, _, admin = dashboard
    a, ah = await customer(db, "a@gmail.com")
    b, bh = await customer(db, "b@gmail.com")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        key = await issue(client, bh)
        saved = await client.post(
            "/api/v1/dashboard/credentials",
            headers=admin,
            json={"label": "Foreign key", "kind": "app", "secret": key["secret"]},
        )
        assert saved.status_code == 200
        row = (
            await db.execute(
                select(ServiceCredential).where(ServiceCredential.label == "Foreign key")
            )
        ).scalar_one()
        db.add(
            Permission(
                membership_id=a.id,
                resource_type="credential",
                resource_key=str(row.id),
                action="execute",
            )
        )
        await db.commit()
        identifier = base64.urlsafe_b64encode(b"get:/api/v1/audio/models").decode().rstrip("=")
        denied = await client.post(
            f"/api/v1/dashboard/operations/{identifier}", headers=ah, data={"credential": row.id}
        )
        assert denied.status_code == 403
        assert (
            "/api/v1/keys/provider/{provider}/claim"
            not in (await client.get("/api/v1/dashboard/catalog", headers=admin)).json()["spec"][
                "paths"
            ]
        )
        replacement = await issue(client, ah)
        assert (
            await client.post(
                "/api/v1/dashboard/credentials",
                headers=admin,
                json={"label": row.label, "kind": "app", "secret": replacement["secret"]},
            )
        ).status_code == 409
        logs = (await db.execute(select(AuditLog))).scalars().all()
        assert all(key["secret"] not in r.target for r in logs)
