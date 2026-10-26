"""Uploader isolation with disposable SQL and fake B2, including two admin sessions."""

# ruff: noqa: F811
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from app.blog.schema import blog_assets
from app.models.dashboard import DashboardSession, Membership
from app.models.db_models import User
from app.services.dashboard import digest, now
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_media import BASE, b2, dashboard, send, storage  # noqa: F401


@pytest.mark.asyncio
async def test_own_images_listing_and_retry_isolation(dashboard, b2, storage):
    db, member, headers = dashboard
    other_user = User(
        google_id="isolated-other-owner",
        email="other@example.test",
        is_active=True,
        api_access_enabled=False,
    )
    db.add(other_user)
    await db.commit()
    other = Membership(
        user_id=other_user.id,
        email="other@example.test",
        name="Other",
        role="admin",
        status="approved",
        created_at=now(),
    )
    db.add(other)
    await db.commit()
    db.add(
        DashboardSession(
            token_hash=digest("other-token"),
            membership_id=other.id,
            created_at=now(),
            expires_at=now() + timedelta(hours=1),
        )
    )
    await db.commit()
    other_headers = headers | {"Authorization": "Bearer other-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = str(uuid.uuid4())
        mine = await send(client, headers, token)
        assert mine.status_code == 201, mine.text
        theirs = await send(client, other_headers)
        assert theirs.status_code == 201, theirs.text
        owner_id = mine.json()["asset"]["id"]
        for auth, expected in [(headers, owner_id), (other_headers, theirs.json()["asset"]["id"])]:
            listing = await client.get(BASE + "?media_type=image&page_size=1", headers=auth)
            assert listing.status_code == 200, listing.text
            assert listing.json()["total"] == 1
            assert [a["id"] for a in listing.json()["assets"]] == [expected]
            assert (
                await client.get(BASE + "?media_type=image&page=2&page_size=1", headers=auth)
            ).json()["assets"] == []
        assert (await send(client, other_headers, token)).status_code == 409
        assert b2.puts == 2
        assert (await send(client, headers, token)).json()["reused"] is True
        # Legacy/unattributed and other media types must not leak into the image gallery.
        with storage[0]["azeemlab"].begin() as conn:
            conn.execute(
                blog_assets.insert().values(
                    object_key="legacy",
                    public_url="https://media.test/legacy.png",
                    original_filename="legacy.png",
                    media_type="image/png",
                    size_bytes=1,
                )
            )
            conn.execute(
                blog_assets.insert().values(
                    object_key="audio",
                    public_url="https://media.test/own.wav",
                    original_filename="own.wav",
                    media_type="audio/wav",
                    size_bytes=1,
                    uploaded_by_membership_id=member.id,
                )
            )
        result = (await client.get(BASE + "?media_type=image", headers=headers)).json()
        assert result["total"] == 1
        assert (await client.get(BASE, headers=headers)).json()["total"] == 2
        assert (await client.get(BASE, headers=other_headers)).json()["total"] == 1
        assert (await client.get(BASE, headers={})).status_code == 401
