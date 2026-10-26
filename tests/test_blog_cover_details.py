"""Disposable app DB acceptance for post-scoped cover metadata; no live writes."""
# ruff: noqa: F811
import pytest
from app.blog.schema import blog_assets
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_api import BASE, create, storage
from tests.test_dashboard import dashboard

DETAILS = {"alt_text": "A workflow diagram", "attribution": {"credit": "Jane", "sourceUrl": "https://example.test/photo", "license": "CC BY 4.0", "licenseUrl": "https://example.test/license"}}

@pytest.mark.asyncio
async def test_cover_details_roundtrip_preserve_replace_remove(dashboard, storage):
    _, _, headers = dashboard
    with storage[0]["azeemlab"].begin() as conn:
        for identifier in (1, 2):
            conn.execute(blog_assets.insert().values(id=identifier, object_key=f"blogs/{identifier}.png", public_url=f"https://media.test/{identifier}.png", original_filename="image.png", media_type="image/png", size_bytes=100))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, headers, cover_asset_id=1, cover_image=DETAILS)
        assert response.status_code == 201, response.text
        post = response.json()["post"]
        path = BASE + f"/apps/azeemlab/posts/{post['id']}"
        assert post["metadata_json"]["cover_image"] == {**DETAILS, "asset_id": 1, "url": "https://media.test/1.png"}
        assert post["metadata_json"]["open_graph"]["image_alt"] == DETAILS["alt_text"]
        loaded = (await client.get(path, headers=headers)).json()["post"]
        assert loaded["metadata_json"]["cover_image"] == post["metadata_json"]["cover_image"]
        changed = await client.patch(path, headers=headers, json={"version": 1, "title": "Updated title"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["post"]["metadata_json"]["cover_image"]["attribution"] == DETAILS["attribution"]
        replaced = await client.patch(path, headers=headers, json={"version": 2, "cover_asset_id": 2})
        assert replaced.status_code == 200, replaced.text
        assert "cover_image" not in replaced.json()["post"]["metadata_json"]
        edited = await client.patch(path, headers=headers, json={"version": 3, "cover_image": DETAILS})
        assert edited.status_code == 200, edited.text
        assert edited.json()["post"]["metadata_json"]["cover_image"]["asset_id"] == 2
        removed = await client.patch(path, headers=headers, json={"version": 4, "cover_asset_id": None})
        assert removed.status_code == 200, removed.text
        assert "cover_image" not in removed.json()["post"]["metadata_json"]

@pytest.mark.asyncio
async def test_cover_details_reject_unsafe_or_unbound_values(dashboard, storage):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = [DETAILS, {"alt_text": "x" * 1001}, {"url": "https://untrusted.test/photo"}, {"attribution": {"credit": "Jane", "sourceUrl": "javascript:alert(1)"}}, {"attribution": {"licenseUrl": "https://example.test/license"}}]
        for details in invalid:
            assert (await create(client, headers, cover_image=details)).status_code == 422

@pytest.mark.asyncio
async def test_empty_cover_details_have_safe_defaults(dashboard, storage):
    _, _, headers = dashboard
    with storage[0]["azeemlab"].begin() as conn:
        conn.execute(blog_assets.insert().values(id=1, object_key="blogs/1.png", public_url="https://media.test/1.png", original_filename="image.png", media_type="image/png", size_bytes=100))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, headers, cover_asset_id=1, cover_image={})
        assert response.status_code == 201, response.text
        assert response.json()["post"]["metadata_json"]["cover_image"]["alt_text"] == ""
