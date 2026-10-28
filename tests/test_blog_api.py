"""Phase 3 API contracts with separate disposable app databases; no live writes."""

# ruff: noqa: F811
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from app.blog import router as routes
from app.blog.config import load_registry
from app.blog.migrations.schema_v3 import build_metadata
from app.models.dashboard import Permission
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy.orm import Session

from tests.test_app_authorization import AsyncAdapter
from tests.test_dashboard import dashboard  # noqa: F401

BASE = "/api/v1/dashboard/blog"
DOC = {
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "A useful article about secure apps."}],
        }
    ],
}


class BlogAdapter(AsyncAdapter):
    @asynccontextmanager
    async def begin_nested(self):
        with self.session.begin_nested():
            yield


@pytest.fixture
def storage(monkeypatch, dashboard):
    # Existing publication regressions use an explicitly complete isolated author.
    db, membership, _ = dashboard
    membership.designation = "Editor"
    membership.avatar_url = "https://media.test/author.jpg"
    membership.description = "An experienced author writing about secure applications."
    db.session.commit()
    engines = {}
    connections = []
    for key in ("azeemlab", "ailettergen"):
        engine = sa.create_engine(
            "sqlite://", execution_options={"schema_translate_map": {"public": None}}
        )

        @sa.event.listens_for(engine, "connect")
        def sqlite_connect(connection, _):
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")

        @sa.event.listens_for(engine, "begin")
        def sqlite_begin(connection):
            connection.exec_driver_sql("BEGIN")

        with engine.begin() as connection:
            build_metadata(None).create_all(connection)
            connection.exec_driver_sql("CREATE TABLE blog_schema_version (version_num VARCHAR(32))")
            connection.exec_driver_sql("INSERT INTO blog_schema_version VALUES ('blog_0003')")
        engines[key] = engine
    registry = load_registry(
        {
            f"BLOG_{key.upper()}_DATABASE_URL": f"postgresql://fake:fake@offline.test/{key}"
            for key in engines
        }
        | {f"BLOG_{key.upper()}_PUBLIC_BLOG_URL": f"https://{key}.test/en/blog" for key in engines}
    )
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[routes.registry_dependency] = lambda: registry

    @asynccontextmanager
    async def isolated_session(registry, key, engine_cache=None):
        connections.append(key)
        with Session(engines[key]) as session:
            try:
                yield BlogAdapter(session)
            except BaseException:
                session.rollback()
                raise

    monkeypatch.setattr(routes, "session_for_app", isolated_session)
    yield engines, connections
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    for engine in engines.values():
        engine.dispose()


async def create(client, headers, **changes):
    return await client.post(
        BASE + "/apps/azeemlab/posts",
        headers=headers,
        json={"title": "Hello Secure World", "content_json": DOC, **changes},
    )


@pytest.mark.asyncio
async def test_save_publish_version_and_app_isolation(dashboard, storage):
    db, membership, headers = dashboard
    membership.designation = "Editor"
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await create(client, headers, category_name="Engineering")
        assert first.status_code == 201, first.text
        post = first.json()["post"]
        assert post["slug"] == "hello-secure-world"
        assert post["author_name"] == "Admin" and post["author_designation"] == "Editor"
        assert "author_id" not in post and "content_html" not in post
        assert post["author_description"] == membership.description
        assert post["metadata_json"]["author_description"] == membership.description
        assert post["content_json"] == DOC
        assert post["metadata_json"]["robots"]["index"] is False
        duplicate = (await create(client, headers, category_name="ENGINEERING")).json()["post"]
        assert duplicate["slug"] != post["slug"]
        cats = await client.get(BASE + "/apps/azeemlab/categories", headers=headers)
        assert cats.json()["total"] == 1
        other = await client.get(BASE + "/apps/ailettergen/posts", headers=headers)
        assert other.json()["total"] == 0
        path = BASE + f"/apps/azeemlab/posts/{post['id']}"
        # Saving refreshes the original author's snapshot from their current profile.
        changed_profile = await client.patch(
            BASE + "/profile", headers=headers, json={"description": "New profile biography."}
        )
        assert changed_profile.status_code == 200
        published = await client.patch(
            path, headers=headers, json={"version": 1, "status": "published"}
        )
        assert published.status_code == 200, published.text
        saved = published.json()["post"]
        assert saved["version"] == 2 and saved["published_at"]
        assert saved["author_description"] == "New profile biography."
        assert saved["metadata_json"]["author_description"] == "New profile biography."
        fetched = (await client.get(path, headers=headers)).json()["post"]
        assert fetched["author_description"] == "New profile biography."
        assert (
            saved["metadata_json"]["canonical_url"]
            == "https://azeemlab.test/en/blog/hello-secure-world"
        )
        assert saved["metadata_json"]["json_ld"]["author"]["jobTitle"] == "Editor"
        stale = await client.patch(path, headers=headers, json={"version": 1, "title": "Stale"})
        assert stale.status_code == 409
        changed = await client.patch(
            path, headers=headers, json={"version": 2, "title": "New title"}
        )
        assert changed.json()["post"]["slug"] == post["slug"]
        assert (await client.delete(path, headers=headers)).status_code == 405
        assert first.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_permissions_fail_before_database_and_publish_is_separate(dashboard, storage):
    db, membership, headers = dashboard
    membership.role = "viewer"
    await db.commit()

    def grant(kind, key):
        db.add(
            Permission(
                membership_id=membership.id, resource_type=kind, resource_key=key, action="view"
            )
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await create(client, headers)).status_code == 403
        grant("page", "blog-editor")
        grant("component", "blog.app.azeemlab")
        await db.commit()
        assert (await create(client, headers)).status_code == 403
        assert storage[1] == []
        grant("component", "blog.create")
        await db.commit()
        assert (await create(client, headers)).status_code == 201
        assert (await create(client, headers, status="published")).status_code == 403
        assert (
            await client.get(BASE + "/apps/ailettergen/posts", headers=headers)
        ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"slug": "caller-slug"},
        {"author_name": "Spoof"},
        {"author_description": "Spoof biography"},
        {"metadata_json": {}},
        {"title": None},
        {"content_json": {"type": "doc", "content": [{"type": "script"}]}},
        {
            "content_json": {
                "type": "doc",
                "content": [{"type": "customImage", "attrs": {"src": "data:image/svg+xml,bad"}}],
            }
        },
        {"category_id": True},
        {"category_id": 999},
        {"cover_asset_id": 999},
    ],
)
async def test_untrusted_inputs_rejected_without_posts(dashboard, storage, changes):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, headers, **changes)
        assert response.status_code == 422, response.text
        assert (await client.get(BASE + "/apps/azeemlab/posts", headers=headers)).json()[
            "total"
        ] == 0


@pytest.mark.asyncio
async def test_origin_body_limit_locale_and_profile(dashboard, storage):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await create(client, headers | {"Origin": "https://evil.test"})).status_code == 403
        oversized = await client.post(
            BASE + "/apps/azeemlab/posts",
            headers=headers | {"Content-Type": "application/json"},
            content=b" " * (2 * 1024 * 1024 + 1),
        )
        assert oversized.status_code == 413
        assert (await create(client, headers, status="published", locale="fr")).status_code == 409
        profile = await client.patch(
            BASE + "/profile",
            headers=headers,
            json={"name": "Writer", "designation": "Lead author"},
        )
        assert profile.status_code == 200, profile.text
        assert (await create(client, headers)).json()["post"]["author_name"] == "Writer"
        assert (
            await client.patch(BASE + "/profile", headers=headers, json={"role": "admin"})
        ).status_code == 422


@pytest.mark.asyncio
async def test_failed_post_rolls_back_new_category_and_assets_are_server_owned(dashboard, storage):
    from app.blog.schema import blog_assets

    _, _, headers = dashboard
    with storage[0]["azeemlab"].begin() as connection:
        connection.execute(
            blog_assets.insert().values(
                id=1,
                object_key="blog/image.png",
                public_url="https://media.test/blog/image.png",
                original_filename="image.png",
                media_type="image/png",
                size_bytes=100,
            )
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await create(
            client, headers, category_name="Rollback category", cover_asset_id=999
        )
        assert failed.status_code == 422
        assert (await client.get(BASE + "/apps/azeemlab/categories", headers=headers)).json()[
            "total"
        ] == 0
        image = {
            "type": "doc",
            "content": [
                {
                    "type": "customImage",
                    "attrs": {"asset_id": 1, "src": "https://untrusted.test/fake.png"},
                }
            ],
        }
        saved = await create(client, headers, content_json=image, cover_asset_id=1)
        assert saved.status_code == 201, saved.text
        post = saved.json()["post"]
        assert (
            post["content_json"]["content"][0]["attrs"]["src"]
            == "https://media.test/blog/image.png"
        )
        assert post["metadata_json"]["open_graph"]["image"] == "https://media.test/blog/image.png"
        foreign = await client.post(
            BASE + "/apps/ailettergen/posts",
            headers=headers,
            json={"title": "Foreign asset", "content_json": image},
        )
        assert foreign.status_code == 422


@pytest.mark.asyncio
async def test_audit_outage_preserves_committed_success(dashboard, storage, monkeypatch):
    from unittest.mock import AsyncMock

    from app.blog import service

    _, _, headers = dashboard
    monkeypatch.setattr(
        service, "audit", AsyncMock(side_effect=RuntimeError("private database details"))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, headers)
        assert response.status_code == 201
        assert response.json()["audit_recorded"] is False
        assert "private database details" not in response.text
        assert (await client.get(BASE + "/apps/azeemlab/posts", headers=headers)).json()[
            "total"
        ] == 1


@pytest.mark.asyncio
async def test_phase6_update_and_publish_grants_cannot_be_bypassed(dashboard, storage):
    db, membership, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        published = (await create(client, headers, status="published")).json()["post"]
        membership.role = "viewer"
        for kind, key in [
            ("page", "blog-editor"),
            ("component", "blog.app.azeemlab"),
            ("component", "blog.create"),
        ]:
            db.add(
                Permission(
                    membership_id=membership.id, resource_type=kind, resource_key=key, action="view"
                )
            )
        await db.commit()
        draft = await create(client, headers)
        assert draft.status_code == 201
        path = BASE + f"/apps/azeemlab/posts/{draft.json()['post']['id']}"
        assert (
            await client.patch(
                path, headers=headers, json={"version": 1, "title": "No update permission"}
            )
        ).status_code == 403
        db.add(
            Permission(
                membership_id=membership.id,
                resource_type="component",
                resource_key="blog.update",
                action="view",
            )
        )
        await db.commit()
        assert (
            await client.patch(
                path, headers=headers, json={"version": 1, "title": "Allowed draft edit"}
            )
        ).status_code == 200
        published_path = BASE + f"/apps/azeemlab/posts/{published['id']}"
        assert (
            await client.patch(
                published_path,
                headers=headers,
                json={"version": 1, "title": "Forbidden published edit"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                published_path, headers=headers, json={"version": 1, "status": "draft"}
            )
        ).status_code == 403
        assert (
            await client.get(BASE + "/apps/ailettergen/assets", headers=headers)
        ).status_code == 403
        assert (await client.delete(published_path, headers=headers)).status_code == 405


@pytest.mark.asyncio
async def test_direct_blog_requests_recheck_session_revocation_without_me(dashboard, storage):
    from app.models.dashboard import DashboardSession
    from app.services.dashboard import now

    db, membership, headers = dashboard
    _, connections = storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = BASE + "/apps/azeemlab/categories"
        assert (await client.get(path, headers=headers)).status_code == 200
        connections.clear()
        session = (await db.execute(sa.select(DashboardSession))).scalar_one()
        session.revoked_at = now()
        await db.commit()
        assert (await client.get(path, headers=headers)).status_code == 401
        assert (await client.get(BASE + "/profile", headers=headers)).status_code == 401
        assert not connections


@pytest.mark.asyncio
async def test_author_profile_uses_current_users_row_and_refreshes_saved_author(dashboard, storage):
    from app.models.db_models import User

    db, membership, headers = dashboard
    owner = await db.get(User, membership.user_id)
    owner.name = "Current Account Name"
    membership.name = "Old Membership Name"
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        profile = await client.get(BASE + "/profile", headers=headers)
        assert profile.json()["name"] == "Current Account Name"
        saved = (await create(client, headers)).json()["post"]
        assert saved["author_name"] == "Current Account Name"
        owner.name = "Updated Account Name"
        await db.commit()
        changed = await client.patch(
            BASE + f"/apps/azeemlab/posts/{saved['id']}",
            headers=headers,
            json={"version": 1, "title": "Edited article"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["post"]["author_name"] == "Updated Account Name"
        assert (await client.get(BASE + "/profile", headers=headers)).json()[
            "name"
        ] == "Updated Account Name"


@pytest.mark.asyncio
async def test_title_limit_is_seventy_for_create_and_update(dashboard, storage):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, headers, title="A" * 70)
        assert response.status_code == 201
        assert (await create(client, headers, title="A" * 71)).status_code == 422
        post = response.json()["post"]
        rejected = await client.patch(
            BASE + f"/apps/azeemlab/posts/{post['id']}",
            headers=headers,
            json={"version": 1, "title": "B" * 71},
        )
        assert rejected.status_code == 422
