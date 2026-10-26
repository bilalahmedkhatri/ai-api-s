"""Phase 4 uses isolated SQL and fake B2; provider credentials never used."""

# ruff: noqa: F811
import io
import uuid
import wave
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from app.blog import media
from app.blog import router as routes
from app.blog.storage import storage_configuration
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from main import app

from tests.test_blog_api import BlogAdapter, storage  # noqa: F401
from tests.test_dashboard import dashboard  # noqa: F401

BASE = "/api/v1/dashboard/blog/apps/azeemlab/assets"
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


class FakeStorage:
    bucket = "blogPost001"

    def __init__(self):
        self.objects = {}
        self.puts = 0
        self.deleted = []
        self.fail = False

    def url(self, key):
        return "https://media.test/" + key

    @asynccontextmanager
    async def client(self):
        yield self

    async def put_object(self, **kwargs):
        self.puts += 1
        self.objects[kwargs["Key"]] = kwargs["Metadata"]
        if self.fail:
            raise RuntimeError("private provider error")

    async def head_object(self, **kwargs):
        return {"Metadata": self.objects[kwargs["Key"]]}

    async def delete_object(self, **kwargs):
        self.deleted.append(kwargs["Key"])
        self.objects.pop(kwargs["Key"], None)


@pytest.fixture
def b2(storage, monkeypatch):
    fake = FakeStorage()
    app.dependency_overrides[routes.storage_dependency] = lambda: fake
    monkeypatch.setattr(
        media, "probe", AsyncMock(return_value={"width": 1, "height": 1, "duration_seconds": None})
    )
    yield fake
    app.dependency_overrides.pop(routes.storage_dependency, None)


async def send(client, headers, token=None, content=PNG, mime="image/png", path=BASE):
    return await client.post(
        path + "?filename=test.png",
        headers=headers | {"Idempotency-Key": token or str(uuid.uuid4()), "Content-Type": mime},
        content=content,
    )


@pytest.mark.asyncio
async def test_upload_retry_hash_and_app_scoped_reference(dashboard, b2):
    _, _, headers = dashboard
    token = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await send(client, headers, token)
        assert first.status_code == 201, first.text
        asset = first.json()["asset"]
        assert asset["object_key"] == f"blogs/azeemlab/{token}.png"
        assert asset["public_url"] == f"https://media.test/blogs/azeemlab/{token}.png"
        assert b2.puts == 1 and not b2.deleted
        second = await send(client, headers, token)
        assert second.status_code == 201 and second.json()["reused"]
        assert second.json()["asset"]["id"] == asset["id"] and b2.puts == 1
        changed = await send(client, headers, token, content=PNG[:-1] + b"x")
        assert changed.status_code == 409
        assert (await client.get(BASE, headers=headers)).json()["total"] == 1
        other = BASE.replace("azeemlab", "ailettergen")
        assert (await client.get(other, headers=headers)).json()["total"] == 0
        assert (
            await client.delete(BASE + "/" + str(asset["id"]), headers=headers)
        ).status_code in (404, 405)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime,data,status",
    [
        ("image/svg+xml", b"<svg/>", 415),
        ("image/png", b"<script>bad</script>", 422),
        ("image/png", b"", 422),
        ("image/png", b"oversize", 413),
    ],
)
async def test_rejected_before_upload(dashboard, b2, mime, data, status):
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await send(
            client,
            headers,
            mime=mime,
            content=PNG + b"x" * (10 * 1024**2) if data == b"oversize" else data,
        )
        assert response.status_code == status, response.text
        assert b2.puts == 0


@pytest.mark.asyncio
async def test_upload_error_compensates_and_hides_provider_details(dashboard, b2):
    _, _, headers = dashboard
    b2.fail = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await send(client, headers)
        assert response.status_code == 503 and "private provider" not in response.text
        assert len(b2.deleted) == 1
        assert (await client.get(BASE, headers=headers)).json()["total"] == 0


@pytest.mark.asyncio
async def test_permission_and_origin_fail_before_probe_or_b2(dashboard, b2):
    db, member, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await send(client, headers | {"Origin": "https://evil.test"})).status_code == 403
        member.role = "viewer"
        await db.commit()
        assert (await send(client, headers)).status_code == 403
        assert b2.puts == 0 and media.probe.await_count == 0


@pytest.mark.asyncio
async def test_real_local_probe_accepts_wav_and_rejects_fake_png(tmp_path):
    file = tmp_path / "audio.wav"
    with wave.open(str(file), "wb") as stream:
        stream.setparams((1, 2, 8000, 800, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 800)
    result = await media.probe(str(file), "audio/wav")
    assert result["duration_seconds"] == pytest.approx(0.1)
    bad = tmp_path / "bad.png"
    bad.write_bytes(PNG)
    with pytest.raises(HTTPException) as error:
        await media.probe(str(bad), "image/png")
    assert error.value.status_code == 422


def test_configuration_does_not_fall_back_to_shared_storage():
    with pytest.raises(HTTPException):
        storage_configuration({"B2_BUCKET_NAME": "private-audio"})
    valid = {
        "BLOG_B2_BUCKET_NAME": "blogPost001",
        "BLOG_B2_APPLICATION_KEY_ID": "secret-id",
        "BLOG_B2_APPLICATION_KEY": "secret-key",
        "BLOG_B2_ENDPOINT_URL": "https://s3.us-west-004.backblazeb2.com",
        "BLOG_B2_PUBLIC_BASE_URL": "https://cdn.test/file/blogPost001",
    }
    config = storage_configuration(valid)
    assert (
        config.url("blogs/azeemlab/a.png")
        == "https://cdn.test/file/blogPost001/blogs/azeemlab/a.png"
    )
    assert "secret-key" not in repr(config) and "secret-id" not in repr(config)
    with pytest.raises(HTTPException):
        storage_configuration(valid | {"BLOG_B2_PUBLIC_BASE_URL": "https://cdn.test/?token=secret"})


@pytest.mark.asyncio
async def test_definite_insert_failure_cleans_object(dashboard, b2, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.sql.dml import Insert

    original = BlogAdapter.execute

    async def fail_insert(self, statement):
        if isinstance(statement, Insert) and statement.table.name == "blog_assets":
            raise SQLAlchemyError("private SQL details")
        return await original(self, statement)

    monkeypatch.setattr(BlogAdapter, "execute", fail_insert)
    _, _, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await send(client, headers)
        assert response.status_code == 503 and "private SQL" not in response.text
        assert len(b2.deleted) == 1


@pytest.mark.asyncio
async def test_ambiguous_commit_preserves_object_and_same_token_can_retry(
    dashboard, b2, monkeypatch
):
    original = BlogAdapter.commit
    attempts = 0

    async def fail_commit(self):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("commit response lost")
        await original(self)

    monkeypatch.setattr(BlogAdapter, "commit", fail_commit)
    _, _, headers = dashboard
    token = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await send(client, headers, token)
        assert response.status_code == 503
        assert not b2.deleted and len(b2.objects) == 1
        retry = await send(client, headers, token)
        assert retry.status_code == 201, retry.text
        assert (await client.get(BASE, headers=headers)).json()["total"] == 1


@pytest.mark.asyncio
async def test_streamed_limit_without_content_length(dashboard, b2):
    _, _, headers = dashboard

    async def chunks():
        yield PNG
        for _ in range(11):
            yield b"x" * 1024**2

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            BASE + "?filename=large.png",
            headers=headers | {"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "image/png"},
            content=chunks(),
        )
        assert response.status_code == 413 and b2.puts == 0


@pytest.mark.asyncio
async def test_orphan_reconciliation_preserves_registered_and_recent_objects(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.blog import orphans

    fake = FakeStorage()
    fake.head_object = AsyncMock(return_value={"LastModified": datetime.now(UTC)})
    fake.delete_object = AsyncMock()
    db = type("Database", (), {"execute": AsyncMock()})()

    @asynccontextmanager
    async def session(*args):
        yield db

    monkeypatch.setattr(orphans, "storage_configuration", lambda: fake)
    monkeypatch.setattr(orphans, "load_registry", lambda: None)
    monkeypatch.setattr(orphans, "session_for_app", session)
    monkeypatch.setattr(orphans, "lookup", AsyncMock(return_value={"id": 1}))
    key = "blogs/azeemlab/" + str(uuid.uuid4()) + ".png"
    assert (await orphans.reconcile("azeemlab", key, True))["status"] == "registered_preserved"
    fake.head_object.assert_not_awaited()
    monkeypatch.setattr(orphans, "lookup", AsyncMock(return_value=None))
    assert (await orphans.reconcile("azeemlab", key, True))["status"] == "recent_preserved"
    fake.head_object.return_value = {
        "LastModified": datetime.now(UTC) - timedelta(days=2),
        "VersionId": "orphan-version",
    }
    assert (await orphans.reconcile("azeemlab", key))["status"] == "stale_orphan"
    fake.delete_object.assert_not_awaited()
    assert (await orphans.reconcile("azeemlab", key, True))["status"] == "stale_orphan_deleted"
    assert fake.delete_object.call_args.kwargs["VersionId"] == "orphan-version"
    with pytest.raises(ValueError):
        await orphans.reconcile("azeemlab", key.replace("azeemlab", "ailettergen"), True)
