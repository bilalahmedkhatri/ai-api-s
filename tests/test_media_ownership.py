"""Tenant-scoped media reads and deletes use isolated SQL and a fake storage SDK."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.models.db_models import ExtractedMedia
from app.services import media_extractor as media
from sqlalchemy import select


@pytest.fixture
def storage(unit_db, monkeypatch):
    client = SimpleNamespace(
        delete_object=AsyncMock(),
        generate_presigned_url=AsyncMock(
            side_effect=lambda _, Params, **kw: "https://storage.test/bucket/" + Params["Key"]
        ),
    )

    class Context:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(media, "AsyncSessionLocal", lambda: unit_db)
    monkeypatch.setattr(
        media, "get_b2_session", lambda: (SimpleNamespace(client=lambda *a, **kw: Context()), {})
    )
    monkeypatch.setattr(settings, "b2_bucket_name", "bucket")
    return unit_db, client


async def test_app_isolation_and_legacy_media_unavailable(storage):
    db, client = storage
    for app_id in (None, 1, 2):
        db.add(
            ExtractedMedia(
                user_id="same-user",
                item_id="same-item",
                authorized_app_id=app_id,
                object_key=f"media/app-{app_id}/same-user/file.jpg",
            )
        )
    await db.commit()
    urls = await media.get_media_urls_for_item("same-user", "same-item", authorized_app_id=1)
    assert len(urls) == 1 and "app-1/" in urls[0]
    assert client.generate_presigned_url.await_count == 1
    urls = await media.get_media_urls_for_item("same-user", "same-item", authorized_app_id=3)
    assert urls == []


async def test_delete_requires_owned_record_and_keeps_failed_storage_rows(storage):
    db, client = storage
    prefix = "media/app-1/same-user/"
    for app_id, filename in ((1, "ok.jpg"), (1, "failed.jpg"), (2, "foreign.jpg")):
        db.add(
            ExtractedMedia(
                user_id="same-user",
                item_id="item",
                authorized_app_id=app_id,
                object_key=f"media/app-{app_id}/same-user/{filename}",
            )
        )
    await db.commit()

    async def delete(**kwargs):
        if kwargs["Key"].endswith("failed.jpg"):
            raise RuntimeError("Simulated storage failure")

    client.delete_object.side_effect = delete
    urls = [
        f"https://storage.test/bucket/{prefix}{name}"
        for name in ("ok.jpg", "failed.jpg", "unowned.jpg")
    ]
    urls.append("https://storage.test/bucket/media/app-2/same-user/foreign.jpg")
    result = await media.delete_media_urls("same-user", urls, authorized_app_id=1)
    assert result == {"deleted": 1, "failed": 1}
    assert client.delete_object.await_count == 2
    remaining = (await db.execute(select(ExtractedMedia.object_key))).scalars().all()
    assert prefix + "failed.jpg" in remaining and prefix + "ok.jpg" not in remaining
    assert "media/app-2/same-user/foreign.jpg" in remaining


async def test_forged_url_does_not_call_storage(storage):
    _, client = storage
    result = await media.delete_media_urls(
        "victim",
        ["https://storage.test/bucket/media/app-1/victim/guessed.jpg"],
        authorized_app_id=1,
    )
    assert result["deleted"] == 0
    client.delete_object.assert_not_awaited()


@pytest.mark.parametrize("value", ["..", ".", "../victim", "bad\\path", "bad\x00id"])
def test_media_path_identifiers_reject_traversal(value):
    from app.api.v1.media.schemas import ProcessMediaRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProcessMediaRequest(user_id=value, item_id="item", keywords=[])
