"""Publishing checks use isolated membership/app databases and no real uploads."""

# ruff: noqa: F811
import pytest
import sqlalchemy as sa
from app.blog import router as routes
from app.blog.schema import blog_posts
from app.models.dashboard import Membership
from app.models.db_models import User
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_api import BASE, DOC, create, storage  # noqa: F401
from tests.test_dashboard import dashboard  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "avatar_url", "designation", "description"])
async def test_every_required_field_blocks_publication_but_not_drafts(dashboard, storage, field):
    db, membership, headers = dashboard
    setattr(membership, field, " " if field == "name" else None)
    if field == "name":
        owner = await db.get(User, membership.user_id)
        owner.name = "Verified Account Name"
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        draft = await create(client, headers)
        assert draft.status_code == 201, draft.text
        before = (await client.get(BASE + "/apps/azeemlab/posts", headers=headers)).json()["total"]
        rejected = await create(client, headers, status="published")
        assert rejected.status_code == 422
        assert "profile" in rejected.json()["detail"]
        assert (await client.get(BASE + "/apps/azeemlab/posts", headers=headers)).json()[
            "total"
        ] == before
        post = draft.json()["post"]
        assert (
            await client.patch(
                BASE + f"/apps/azeemlab/posts/{post['id']}",
                headers=headers,
                json={"version": 1, "status": "published"},
            )
        ).status_code == 422
        profile = (await client.get(BASE + "/profile", headers=headers)).json()
        assert profile["complete"] is False and field in profile["missing_fields"]


@pytest.mark.asyncio
async def test_self_profile_completion_allows_publication_and_preserves_other_members(
    dashboard, storage
):
    db, membership, headers = dashboard
    membership.description = None
    other = Membership(
        email="other@test.invalid",
        name="Other Author",
        designation="Other",
        description="Original biography",
        role="viewer",
        status="approved",
        created_at=membership.created_at,
    )
    db.add(other)
    await db.commit()
    body = {
        "name": "Current Author",
        "avatar_url": "https://media.test/current.png",
        "designation": "Technical Writer",
        "description": "Writes practical guides.\nReviews real implementation details.",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(BASE + "/profile", headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["complete"] is True and response.json()["missing_fields"] == []
        assert response.json()["avatar_url"] == body["avatar_url"]
        assert other.description == "Original biography" and other.name == "Other Author"
        owner = await db.get(User, membership.user_id)
        assert owner.name == "Current Author"
        published = await create(client, headers, status="published")
        assert published.status_code == 201, published.text
        post = published.json()["post"]
        membership.description = None
        await db.commit()
        path = BASE + f"/apps/azeemlab/posts/{post['id']}"
        assert (
            await client.patch(
                path, headers=headers, json={"version": 1, "title": "Updated article"}
            )
        ).status_code == 422
        assert (
            await client.patch(path, headers=headers, json={"version": 1, "status": "draft"})
        ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"avatar_url": "javascript:alert(1)"},
        {"avatar_url": "https://user:secret@example.test/a.png"},
        {"description": "A" * 2001},
        {"description": "bad\x00text"},
        {"user_id": 999},
        {"complete": True},
    ],
)
async def test_profile_rejects_unsafe_or_caller_owned_identity_fields(dashboard, storage, payload):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.patch(BASE + "/profile", headers=headers, json=payload)
        ).status_code == 422
