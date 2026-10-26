"""Self-avatar uploads: isolated sessions/SQL and fake B2; no real object writes."""
# ruff: noqa: F811

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from app.blog import media
from app.blog import router as routes
from app.blog.avatar import MAX_BYTES, upload_avatar
from app.models.dashboard import Permission
from botocore.exceptions import BotoCoreError
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_dashboard import dashboard  # noqa: F401

PATH = "/api/v1/dashboard/blog/profile/avatar"
PNG = b"\x89PNG\r\n\x1a\n" + b"isolated"


class FakeStorage:
    bucket = "blogPost001"

    def __init__(self):
        self.objects = {}
        self.fail = False

    @asynccontextmanager
    async def client(self):
        yield self

    def url(self, key):
        return "https://media.test/" + key

    async def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = (kwargs["Body"].read(), kwargs["ContentType"])
        if self.fail:
            raise BotoCoreError()


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(routes, "storage_configuration", lambda: fake)
    monkeypatch.setattr(media, "probe", AsyncMock(return_value={"width": 512, "height": 256}))
    return fake


@pytest.mark.asyncio
async def test_avatar_upload_scoped_to_session_and_does_not_save_profile_implicitly(
    dashboard, storage
):
    _, membership, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            PATH + "?membership_id=999",
            headers=headers | {"Content-Type": "image/png"},
            content=PNG,
        )
        assert first.status_code == 201, first.text
        url = first.json()["url"]
        assert f"/blogs/authors/{membership.id}/" in url and "/999/" not in url
        assert membership.avatar_url is None
        again = await client.post(
            PATH, headers=headers | {"Content-Type": "image/png"}, content=PNG
        )
        assert again.json()["url"] == url and len(storage.objects) == 1
        assert next(iter(storage.objects.values())) == (PNG, "image/png")
        saved = await client.patch(
            "/api/v1/dashboard/blog/profile", headers=headers, json={"avatar_url": url}
        )
        assert saved.status_code == 200 and membership.avatar_url == url


@pytest.mark.asyncio
async def test_session_page_and_origin_denials_precede_storage(dashboard, storage):
    db, membership, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.post(PATH, headers={"Content-Type": "image/png"}, content=PNG)
        ).status_code in {401, 403}
        assert (
            await client.post(
                PATH,
                headers=headers | {"Origin": "https://evil.test", "Content-Type": "image/png"},
                content=PNG,
            )
        ).status_code == 403
        membership.role = "viewer"
        await db.commit()
        assert (
            await client.post(PATH, headers=headers | {"Content-Type": "image/png"}, content=PNG)
        ).status_code == 403
        db.add(
            Permission(
                membership_id=membership.id,
                resource_type="page",
                resource_key="blog-editor",
                action="view",
            )
        )
        await db.commit()
        assert (
            await client.post(PATH, headers=headers | {"Content-Type": "image/png"}, content=PNG)
        ).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime,content,status",
    [
        ("image/svg+xml", b"<svg/>", 415),
        ("image/png", b"invalid", 422),
        ("image/png", b"", 422),
        ("image/png", PNG + bytes(MAX_BYTES), 413),
    ],
    ids=["unsupported-type", "invalid-magic", "empty", "oversized"],
)
async def test_invalid_images_and_oversize_uploads_do_not_write(
    dashboard, storage, mime, content, status
):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            PATH, headers=headers | {"Content-Type": mime}, content=content
        )
        assert response.status_code == status, response.text
        assert not storage.objects


@pytest.mark.asyncio
async def test_probe_and_storage_errors_are_safe(dashboard, storage, monkeypatch):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        monkeypatch.setattr(
            media, "probe", AsyncMock(side_effect=HTTPException(422, "Invalid image"))
        )
        assert (
            await client.post(PATH, headers=headers | {"Content-Type": "image/png"}, content=PNG)
        ).status_code == 422
        assert not storage.objects
        monkeypatch.setattr(media, "probe", AsyncMock(return_value={"width": 1, "height": 1}))
        storage.fail = True
        response = await client.post(
            PATH, headers=headers | {"Content-Type": "image/png"}, content=PNG
        )
        assert response.status_code == 503
        assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_streamed_avatar_limit_without_content_length(storage):
    class Request:
        headers = {"content-type": "image/png"}

        async def stream(self):
            yield PNG
            yield bytes(MAX_BYTES)

    class Member:
        id = 7

    with pytest.raises(HTTPException) as caught:
        await upload_avatar(Request(), Member(), storage)
    assert caught.value.status_code == 413 and not storage.objects


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/png", "image/webp"])
async def test_avatar_endpoint_with_real_image_probe_and_fake_b2(
    dashboard, storage, monkeypatch, tmp_path, mime
):
    import os
    import shutil
    import subprocess

    from tests.test_blog_probe_process import REAL_PROBE, png_bytes

    _, membership, headers = dashboard
    monkeypatch.setattr(media, "probe", REAL_PROBE)
    content = png_bytes()
    if mime == "image/webp":
        if not shutil.which("ffmpeg"):
            pytest.skip("Real WebP fixture requires local ffmpeg")
        source, target = tmp_path / "avatar.png", tmp_path / "avatar.webp"
        source.write_bytes(content)
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-y",
                str(target),
            ],
            check=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        content = target.read_bytes()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            PATH, headers=headers | {"Content-Type": mime}, content=content
        )
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        assert f"/blogs/authors/{membership.id}/" in response.json()["url"]
        assert len(storage.objects) == 1
