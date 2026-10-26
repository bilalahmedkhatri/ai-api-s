"""Real dashboard session/grant checks with isolated SQL and fabricated blog URLs."""
# ruff: noqa: F811

import pytest
from app.blog.config import load_registry
from app.blog.router import registry_dependency
from app.models.dashboard import Permission
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_dashboard import dashboard  # noqa: F401

PATH = "/api/v1/dashboard/blog/apps"


@pytest.fixture(autouse=True)
def fake_blog_configuration():
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[registry_dependency] = lambda: load_registry(
        {"BLOG_AZEEMLAB_DATABASE_URL": "postgresql://writer:fake-password@offline.test/blog"}
    )
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_admin_catalog_is_independent_of_authorized_apps_and_contains_no_secrets(dashboard):
    db, membership, headers = dashboard
    # No AuthorizedApp rows are needed or created by this catalog.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(PATH, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    apps = response.json()["apps"]
    assert len(apps) == 6
    assert apps[0] == {"key": "azeemlab", "label": "AzeemLab", "configured": True}
    assert not next(row for row in apps if row["key"] == "sikanderart")["configured"]
    assert "fake-password" not in response.text and "offline.test" not in response.text
    assert all(set(row) == {"key", "label", "configured"} for row in apps)


@pytest.mark.asyncio
async def test_catalog_denies_missing_session_pending_members_and_missing_page(dashboard):
    db, membership, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(PATH)).status_code == 401
        membership.status = "pending"
        await db.commit()
        assert (await client.get(PATH, headers=headers)).status_code == 403
        membership.status = "approved"
        membership.role = "viewer"
        await db.commit()
        assert (await client.get(PATH, headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_page_permission_does_not_grant_all_blog_apps(dashboard):
    db, membership, headers = dashboard
    membership.role = "viewer"
    db.add(
        Permission(
            membership_id=membership.id,
            resource_type="page",
            resource_key="blog-editor",
            action="view",
        )
    )
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(PATH, headers=headers)).json() == {"apps": []}
        db.add(
            Permission(
                membership_id=membership.id,
                resource_type="component",
                resource_key="blog.app.azeemlab",
                action="view",
            )
        )
        await db.commit()
        response = await client.get(PATH, headers=headers)
        assert response.json()["apps"] == [
            {"key": "azeemlab", "label": "AzeemLab", "configured": True}
        ]


@pytest.mark.asyncio
async def test_management_catalog_exposes_new_page_and_app_scopes_without_auto_grants(dashboard):
    db, membership, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/members", headers=headers)
        assert "blog-editor" in response.json()["pages"]
        assert "blog.app.azeemlab" in response.json()["components"]
        assert (await client.delete(PATH, headers=headers)).status_code == 405


@pytest.mark.asyncio
async def test_existing_member_designation_is_exposed_without_a_separate_author_identity(dashboard):
    db, membership, headers = dashboard
    membership.designation = "Staff writer"
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["designation"] == "Staff writer"
    assert response.json()["id"] == membership.id
