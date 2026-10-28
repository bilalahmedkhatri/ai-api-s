"""Post isolation and aggregate-only website totals in disposable app databases."""

# ruff: noqa: F811
from datetime import timedelta

import pytest
import sqlalchemy as sa
from app.blog.schema import blog_posts
from app.models.dashboard import AuditLog, DashboardSession, Membership, Permission
from app.models.db_models import User
from app.services.dashboard import digest, now
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_api import BASE, create, storage  # noqa: F401
from tests.test_dashboard import dashboard  # noqa: F401


async def operator(db, token):
    owner = User(google_id=f"isolated-blog-{token}", email=f"{token}@example.test", is_active=True)
    db.add(owner)
    db.session.flush()
    member = Membership(
        user_id=owner.id,
        email=f"{token}@example.test",
        name="Admin",
        role="operator",
        status="approved",
        created_at=now(),
    )
    db.add(member)
    db.session.flush()
    db.add(
        DashboardSession(
            token_hash=digest(token),
            membership_id=member.id,
            created_at=now(),
            expires_at=now() + timedelta(hours=1),
        )
    )
    for kind, key in [
        ("page", "blog-editor"),
        ("component", "blog.app.azeemlab"),
        ("component", "blog.app.ailettergen"),
        ("component", "blog.create"),
        ("component", "blog.update"),
    ]:
        db.add(
            Permission(membership_id=member.id, resource_type=kind, resource_key=key, action="view")
        )
    await db.commit()
    return member, {"Origin": "http://localhost:3002", "Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("editor", ["operator", "admin"])
@pytest.mark.parametrize("identity", ["metadata", "audit", "ambiguous", "missing"])
async def test_save_refreshes_only_verified_original_author(dashboard, storage, editor, identity):
    db, admin, admin_headers = dashboard
    creator, creator_headers = await operator(db, f"author-{editor}-{identity}")
    creator.name = "Original writer"
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, creator_headers)
        assert response.status_code == 201, response.text
        original = response.json()["post"]
        path = BASE + f"/apps/azeemlab/posts/{original['id']}"
        if identity != "metadata":
            metadata = original["metadata_json"].copy()
            metadata.pop("created_by_membership_id")
            if identity == "missing":
                metadata["created_by_membership_id"] = 2147483647
            with storage[0]["azeemlab"].begin() as connection:
                connection.execute(
                    blog_posts.update()
                    .where(blog_posts.c.id == original["id"])
                    .values(metadata_json=metadata)
                )
        if identity == "ambiguous":
            db.add(
                AuditLog(
                    actor_membership_id=admin.id,
                    action="BLOG_POST_CREATE",
                    target=f"azeemlab:post:{original['id']}",
                    status=201,
                    created_at=now(),
                )
            )
            await db.commit()
        profile = await client.patch(
            BASE + "/profile",
            headers=creator_headers,
            json={
                "name": "Updated writer",
                "designation": "Backend Developer",
                "description": "Updated original writer biography.",
                "avatar_url": "https://media.test/original-writer.png",
            },
        )
        assert profile.status_code == 200, profile.text
        # Ambiguous/missing ownership cannot be edited by the operator either.
        if editor == "operator" and identity in {"ambiguous", "missing"}:
            denied = await client.patch(
                path,
                headers=creator_headers,
                json={"version": 1, "title": "Cannot edit unverified ownership"},
            )
            assert denied.status_code == 404
            return
        saved = await client.patch(
            path,
            headers=(creator_headers if editor == "operator" else admin_headers),
            json={"version": 1, "title": "Updated article"},
        )
        assert saved.status_code == 200, saved.text
        post = saved.json()["post"]
        if identity in {"metadata", "audit"}:
            assert post["author_name"] == "Updated writer"
            assert post["author_designation"] == "Backend Developer"
            assert post["author_avatar_url"] == "https://media.test/original-writer.png"
            assert post["author_description"] == "Updated original writer biography."
            assert post["metadata_json"]["author_description"] == post["author_description"]
        else:
            for field in (
                "author_name",
                "author_designation",
                "author_avatar_url",
                "author_description",
            ):
                assert post[field] == original[field]


@pytest.mark.asyncio
async def test_operator_list_detail_update_isolation_and_unfiltered_website_counts(
    dashboard, storage
):
    db, admin, admin_headers = dashboard
    first, first_headers = await operator(db, "first")
    second, second_headers = await operator(db, "second")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_post = (await create(client, admin_headers, title="Administrator article")).json()[
            "post"
        ]
        own = (await create(client, first_headers, title="First operator")).json()["post"]
        other = (await create(client, second_headers, title="Second operator")).json()["post"]
        listing = (await client.get(BASE + "/apps/azeemlab/posts", headers=first_headers)).json()
        assert [post["id"] for post in listing["items"]] == [own["id"]]
        assert listing["total"] == 1 and listing["scope"] == "own"
        assert listing["counts"] == {"mine": 1, "total": 3}
        filtered = (
            await client.get(
                BASE + "/apps/azeemlab/posts?search=Administrator&scope=all&membership_id=1",
                headers=first_headers,
            )
        ).json()
        assert filtered["items"] == [] and filtered["total"] == 0
        assert filtered["counts"] == {"mine": 1, "total": 3}
        for post in (admin_post, other):
            path = BASE + f"/apps/azeemlab/posts/{post['id']}"
            assert (await client.get(path, headers=first_headers)).status_code == 404
            assert (
                await client.patch(
                    path, headers=first_headers, json={"version": 1, "title": "Not yours"}
                )
            ).status_code == 404
        changed = await client.patch(
            BASE + f"/apps/azeemlab/posts/{own['id']}",
            headers=first_headers,
            json={"version": 1, "title": "Renamed"},
        )
        assert changed.status_code == 200
        assert changed.json()["post"]["metadata_json"]["created_by_membership_id"] == first.id
        admin_list = (await client.get(BASE + "/apps/azeemlab/posts", headers=admin_headers)).json()
        assert admin_list["total"] == 3 and admin_list["scope"] == "all"
        assert admin_list["counts"] == {"mine": 1, "total": 3}
        assert (
            await create(client, first_headers, created_by_membership_id=second.id)
        ).status_code == 422


@pytest.mark.asyncio
async def test_legacy_audits_require_exact_app_and_unambiguous_creator(dashboard, storage):
    db, admin, headers = dashboard
    member, own_headers = await operator(db, "legacy")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own = (await create(client, own_headers)).json()["post"]
        foreign = (
            await client.post(
                BASE + "/apps/ailettergen/posts",
                headers=headers,
                json={
                    "title": "Other app",
                    "content_json": {"type": "doc", "content": [{"type": "paragraph"}]},
                },
            )
        ).json()["post"]
        for key, identifier in [("azeemlab", own["id"]), ("ailettergen", foreign["id"])]:
            with storage[0][key].begin() as connection:
                connection.execute(
                    blog_posts.update()
                    .where(blog_posts.c.id == identifier)
                    .values(metadata_json={})
                )
        listing = (await client.get(BASE + "/apps/azeemlab/posts", headers=own_headers)).json()
        assert listing["counts"] == {"mine": 1, "total": 1}
        assert len(listing["items"]) == 1
        other_app = (await client.get(BASE + "/apps/ailettergen/posts", headers=own_headers)).json()
        assert other_app["items"] == [] and other_app["counts"] == {"mine": 0, "total": 1}
        db.add(
            AuditLog(
                actor_membership_id=admin.id,
                action="BLOG_POST_CREATE",
                target=f"azeemlab:post:{own['id']}",
                status=201,
                created_at=now(),
            )
        )
        await db.commit()
        hidden = (await client.get(BASE + "/apps/azeemlab/posts", headers=own_headers)).json()
        assert hidden["items"] == [] and hidden["counts"] == {"mine": 0, "total": 1}
        assert (
            await client.get(BASE + f"/apps/azeemlab/posts/{own['id']}", headers=own_headers)
        ).status_code == 404


@pytest.mark.asyncio
async def test_persisted_creator_survives_audit_failure_and_admin_edits(
    dashboard, storage, monkeypatch
):
    from unittest.mock import AsyncMock

    from app.blog import service

    db, _, headers = dashboard
    member, own_headers = await operator(db, "outage")
    monkeypatch.setattr(service, "audit", AsyncMock(side_effect=RuntimeError("audit unavailable")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        saved = await create(client, own_headers)
        assert saved.status_code == 201 and saved.json()["audit_recorded"] is False
        post = saved.json()["post"]
        path = BASE + f"/apps/azeemlab/posts/{post['id']}"
        changed = await client.patch(
            path, headers=headers, json={"version": 1, "title": "Admin edit"}
        )
        assert changed.status_code == 200
        assert changed.json()["post"]["metadata_json"]["created_by_membership_id"] == member.id
        listing = (await client.get(BASE + "/apps/azeemlab/posts", headers=own_headers)).json()
        assert listing["total"] == 1 and listing["counts"] == {"mine": 1, "total": 1}
