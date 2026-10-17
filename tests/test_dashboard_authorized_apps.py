"""Authorized-app dashboard controls use isolated SQL and real session authorization."""
# ruff: noqa: F811
from unittest.mock import AsyncMock

import pytest
from app.models.dashboard import AuditLog
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import func, select

from tests.test_dashboard import dashboard  # noqa: F401

URL = "/api/v1/dashboard/authorized-apps"


@pytest.mark.asyncio
async def test_app_lifecycle_reuses_gateway_owner_and_exact_grants(dashboard):
    db, membership, headers = dashboard
    db.rollback = AsyncMock(side_effect=db.session.rollback)
    owner = User(google_id="second-owner", email="owner@example.test", is_active=True, api_access_enabled=False)
    db.add(owner)
    await db.commit()
    assert owner.id != membership.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = await client.get(URL, headers=headers)
        assert catalog.status_code == 200 and catalog.headers["cache-control"] == "no-store"
        assert "POST /api/v1/generate/" in catalog.json()["endpoints"]
        assert any(row["id"] == owner.id for row in catalog.json()["owners"])
        assert "google_id" not in catalog.text
        created = await client.post(URL, headers=headers, json={
            "name": "Writing app", "owner_user_id": owner.id, "allowed_origin": "https://writing.example:443/",
            "allowed_endpoints": ["POST /api/v1/query/"],
        })
        assert created.status_code == 201
        app_id = created.json()["id"]
        assert created.json()["owner_user_id"] == owner.id
        assert created.json()["allowed_origin"] == "https://writing.example"
        assert not created.json()["is_approved"] and not owner.api_access_enabled
        updated = await client.patch(f"{URL}/{app_id}", headers=headers, json={
            "allowed_endpoints": ["POST /api/v1/query/", "POST /api/v1/generate/"],
            "is_approved": True, "is_active": True,
        })
        assert updated.status_code == 200 and len(updated.json()["allowed_endpoints"]) == 2
        assert not owner.api_access_enabled
        assert (await client.patch(f"{URL}/owners/{owner.id}/permission", headers=headers,
                                   json={"api_access_enabled": True})).status_code == 200
        assert owner.api_access_enabled
        assert (await db.execute(select(func.count()).select_from(AccessAPIKey))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one() == 3


@pytest.mark.asyncio
async def test_invalid_grants_do_not_mutate_apps_or_commit_audit(dashboard):
    db, membership, headers = dashboard
    db.rollback = AsyncMock(side_effect=db.session.rollback)
    body = {"name": "Bad app", "owner_user_id": membership.user_id, "allowed_origin": "https://example.test",
            "allowed_endpoints": ["POST /api/v1/generate/not-registered"]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(URL, headers=headers, json=body)).status_code == 400
        body["allowed_endpoints"] = ["POST /api/v1/generate/*"]
        assert (await client.post(URL, headers=headers, json=body)).status_code == 422
        body["allowed_endpoints"] = []
        body["owner_user_id"] = 99999
        assert (await client.post(URL, headers=headers, json=body)).status_code == 400
        assert (await db.execute(select(func.count()).select_from(AuthorizedApp))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one() == 0


@pytest.mark.asyncio
async def test_admin_session_and_origin_required_for_all_management(dashboard):
    db, membership, headers = dashboard
    body = {"name": "Test app", "owner_user_id": membership.user_id, "allowed_origin": "https://example.test"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(URL)).status_code == 401
        assert (await client.post(URL, headers={**headers, "Origin": "https://foreign.test"}, json=body)).status_code == 403
        membership.role = "viewer"
        await db.commit()
        assert (await client.get(URL, headers=headers)).status_code == 403
        assert (await client.post(URL, headers=headers, json=body)).status_code == 403
        assert (await client.patch(f"{URL}/1", headers=headers, json={"is_active": False})).status_code == 403
        assert (await client.patch(f"{URL}/owners/1/permission", headers=headers,
                                   json={"api_access_enabled": True})).status_code == 403
