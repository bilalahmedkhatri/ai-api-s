"""Bounded raw media ingestion with local probing and app-scoped public objects."""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import uuid
from contextlib import suppress

import sqlalchemy as sa
from fastapi import HTTPException

from app.blog.content import clean_text
from app.blog.probe_process import probe_output
from app.blog.schema import blog_assets
from app.blog.service import record_audit

logger = logging.getLogger(__name__)
SLOTS = asyncio.Semaphore(2)
# MIME, extension, maximum bytes, expected ffprobe codecs.
FORMATS = {
    "image/png": ("png", 10 * 1024**2, {"png"}),
    "image/jpeg": ("jpg", 10 * 1024**2, {"mjpeg"}),
    "image/webp": ("webp", 10 * 1024**2, {"webp"}),
    "video/mp4": ("mp4", 64 * 1024**2, {"h264", "hevc", "av1", "mpeg4"}),
    "video/webm": ("webm", 64 * 1024**2, {"vp8", "vp9", "av1"}),
    "audio/mpeg": ("mp3", 25 * 1024**2, {"mp3"}),
    "audio/wav": (
        "wav",
        25 * 1024**2,
        {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8"},
    ),
    "audio/ogg": ("ogg", 25 * 1024**2, {"opus", "vorbis", "flac"}),
}


def signature(data, mime):
    checks = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        "video/mp4": data[4:8] == b"ftyp",
        "video/webm": data.startswith(b"\x1aE\xdf\xa3"),
        "audio/mpeg": data.startswith(b"ID3")
        or len(data) > 1
        and data[0] == 255
        and data[1] & 224 == 224,
        "audio/wav": data[:4] == b"RIFF" and data[8:12] == b"WAVE",
        "audio/ogg": data.startswith(b"OggS"),
    }
    return checks.get(mime, False)


async def probe(path, mime):
    try:
        returncode, output = await probe_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-format_whitelist",
                {
                    "image/png": "png_pipe",
                    "image/jpeg": "jpeg_pipe",
                    "image/webp": "webp_pipe",
                    "video/mp4": "mov",
                    "video/webm": "matroska,webm",
                    "audio/mpeg": "mp3",
                    "audio/wav": "wav",
                    "audio/ogg": "ogg",
                }[mime],
                "-protocol_whitelist",
                "file",
                "-probesize",
                "5242880",
                "-analyzeduration",
                "5000000",
                "-show_entries",
                "stream=codec_name,codec_type,width,height:format=duration",
                "-of",
                "json",
                path,
            ]
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(503, "Blog media validation timed out. Retry the upload.") from None
    except OSError:
        raise HTTPException(
            503, "Blog media validation requires ffprobe on the admin server."
        ) from None
    try:
        if returncode or len(output) > 65536:
            raise ValueError()
        info = json.loads(output)
        streams = info.get("streams", [])
        expected = "video" if mime.startswith(("image/", "video/")) else "audio"
        selected = [s for s in streams if s.get("codec_type") == expected]
        if (
            not selected
            or selected[0].get("codec_name") not in FORMATS[mime][2]
            or len(streams) > 8
        ):
            raise ValueError()
        if mime.startswith("audio/") and any(s.get("codec_type") != "audio" for s in streams):
            raise ValueError()
        width, height = selected[0].get("width"), selected[0].get("height")
        if expected == "video" and (not width or not height or width * height > 40_000_000):
            raise ValueError()
        duration = None
        if not mime.startswith("image/"):
            duration = float(info.get("format", {}).get("duration", 0))
            if not 0 < duration <= 14400:
                raise ValueError()
        return {"width": width, "height": height, "duration_seconds": duration}
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Invalid or unsupported media content.") from None


async def lookup(db, key):
    row = (
        (await db.execute(sa.select(blog_assets).where(blog_assets.c.object_key == key)))
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def cleanup(client, bucket, key, version=None):
    try:
        await asyncio.wait_for(
            client.delete_object(
                Bucket=bucket, Key=key, **({"VersionId": version} if version else {})
            ),
            15,
        )
    except Exception as error:
        # Object keys are server-generated, not credentials or request content.
        logger.warning(
            "Blog orphan cleanup required object_key=%s error_type=%s", key, type(error).__name__
        )


async def upload(request, scope, db, storage, filename, token):
    mime = request.headers.get("content-type", "").lower().strip()
    if mime not in FORMATS:
        raise HTTPException(415, "Supported: PNG, JPEG, WebP, MP4, WebM, MP3, WAV and Ogg.")
    try:
        token = str(uuid.UUID(token))
        filename = clean_text(filename, 255)
        if "/" in filename or "\\" in filename:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(422, "Provide a UUID Idempotency-Key and a plain filename.") from None
    extension, maximum, _ = FORMATS[mime]
    key = f"blogs/{scope.key}/{token}.{extension}"
    declared = request.headers.get("content-length")
    if declared and (not declared.isascii() or not declared.isdecimal() or len(declared) > 12):
        raise HTTPException(400, "Invalid content length.")
    if declared and int(declared) > maximum:
        raise HTTPException(413, "Media exceeds its upload size limit.")
    if SLOTS.locked():
        raise HTTPException(429, "Blog upload capacity reached. Retry later.")
    async with SLOTS:
        descriptor, path = tempfile.mkstemp(suffix="." + extension, prefix="blog-upload-")
        try:
            size = 0
            digest = hashlib.sha256()
            head = b""
            with os.fdopen(descriptor, "wb") as file:
                async with asyncio.timeout(60):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > maximum:
                            raise HTTPException(413, "Media exceeds its upload size limit.")
                        head = (head + chunk[:32])[:32]
                        digest.update(chunk)
                        file.write(chunk)
            if not size or not signature(head, mime):
                raise HTTPException(422, "Media signature does not match its content type.")
            metadata = await probe(path, mime)
            # The same upload token is serialized across gateway workers before any B2 write.
            if db.get_bind().dialect.name == "postgresql":
                await db.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
                await db.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").bindparams(
                        key=key
                    )
                )
            existing = await lookup(db, key)
            if existing:
                if existing["uploaded_by_membership_id"] != scope.membership.id:
                    raise HTTPException(
                        409, "Upload token is unavailable. Choose a new file upload."
                    )
                if existing["size_bytes"] != size or existing["original_filename"] != filename:
                    raise HTTPException(409, "Upload token already belongs to another file.")
                async with storage.client() as client:
                    try:
                        stored = await client.head_object(Bucket=storage.bucket, Key=key)
                    except Exception:
                        raise HTTPException(
                            503, "Cannot verify the existing upload. Retry later."
                        ) from None
                    if stored.get("Metadata", {}).get("sha256") != digest.hexdigest():
                        raise HTTPException(409, "Upload token already belongs to another file.")
                return {"asset": existing, "reused": True}
            async with storage.client() as client:
                try:
                    with open(path, "rb") as body:
                        uploaded = await client.put_object(
                            Bucket=storage.bucket,
                            Key=key,
                            Body=body,
                            ContentLength=size,
                            ContentType=mime,
                            ContentDisposition=f'inline; filename="{token}.{extension}"',
                            CacheControl="public, max-age=31536000, immutable",
                            Metadata={"sha256": digest.hexdigest()},
                        )
                except BaseException as error:
                    # No DB row existed while this token's advisory lock was held.
                    await asyncio.shield(cleanup(client, storage.bucket, key))
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    logger.warning("Blog upload failed error_type=%s", type(error).__name__)
                    raise HTTPException(
                        503, "Blog upload failed. Retry with the same upload token."
                    ) from None
                try:
                    row = (
                        (
                            await db.execute(
                                blog_assets.insert()
                                .values(
                                    uploaded_by_membership_id=scope.membership.id,
                                    object_key=key,
                                    public_url=storage.url(key),
                                    original_filename=filename,
                                    media_type=mime,
                                    size_bytes=size,
                                    **metadata,
                                )
                                .returning(*blog_assets.c)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    row = dict(row)
                except BaseException:
                    await asyncio.shield(
                        cleanup(client, storage.bucket, key, (uploaded or {}).get("VersionId"))
                    )
                    raise
                try:
                    await db.commit()
                except asyncio.CancelledError:
                    logger.warning("Blog asset commit cancelled; reconcile object_key=%s", key)
                    raise
                except Exception:
                    # A failed commit may have succeeded remotely. Never delete a possibly referenced object.
                    with suppress(Exception):
                        await db.rollback()
                    logger.warning(
                        "Blog asset commit uncertain; reconcile before cleanup object_key=%s", key
                    )
                    raise HTTPException(
                        503, "Asset save uncertain. Retry with the same upload token."
                    ) from None
            recorded = await record_audit(
                scope.central,
                scope.membership,
                "BLOG_ASSET_UPLOAD",
                f"{scope.key}:asset:{row['id']}",
            )
            return {"asset": row, "reused": False, "audit_recorded": recorded}
        finally:
            with suppress(OSError):
                os.unlink(path)
