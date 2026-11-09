"""Bounded raw media ingestion with local probing and app-scoped public objects."""

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from contextlib import suppress
from urllib.parse import quote

import sqlalchemy as sa
from app.blog.content import clean_text, slug_base
from app.blog.media import FORMATS, SLOTS, cleanup, probe, signature
from app.blog.schema import blog_assets
from app.blog.service import record_audit
from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def upload(request, scope, db, storage, filename, token, title):
    mime = request.headers.get("content-type", "").lower().strip()
    if mime not in FORMATS:
        raise HTTPException(415, "Supported: PNG, JPEG, WebP, MP4, WebM, MP3, WAV and Ogg.")
    try:
        token = str(uuid.UUID(token))
        filename = clean_text(filename, 255)
        title = clean_text(title, 70)
        if "/" in filename or "\\" in filename:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(422, "Provide a UUID Idempotency-Key and a plain filename.") from None
    extension, maximum, _ = FORMATS[mime]
    filename = f"{slug_base(title, limit=100)}-{token}.{extension}"
    key = f"blogs/{scope.key}/{filename}"
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
                        key=f"blogs/{scope.key}/{token}"
                    )
                )
            existing_rows = (
                (
                    await db.execute(
                        sa.select(blog_assets).where(
                            blog_assets.c.object_key.startswith(
                                f"blogs/{scope.key}/", autoescape=True
                            ),
                            sa.or_(
                                *(
                                    blog_assets.c.object_key.endswith(
                                        f"{token}.{item[0]}", autoescape=True
                                    )
                                    for item in FORMATS.values()
                                )
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            if len(existing_rows) > 1:
                raise HTTPException(409, "Upload token is ambiguous. Choose a new token.")
            existing = dict(existing_rows[0]) if existing_rows else None
            if existing and existing["object_key"] != key:
                raise HTTPException(409, "Upload token is already bound to another title.")
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
                            ContentDisposition=f"inline; filename=\"image.{extension}\"; filename*=UTF-8''{quote(filename, safe='')}",
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
