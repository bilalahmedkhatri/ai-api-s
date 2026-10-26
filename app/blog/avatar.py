"""Bounded self-avatar upload using existing public B2 storage and image validation."""

import asyncio
import hashlib
import os
import tempfile
from contextlib import suppress

from fastapi import HTTPException

from app.blog import media

MAX_BYTES = 2 * 1024 * 1024


async def upload_avatar(request, membership, storage):
    mime = request.headers.get("content-type", "").strip().lower()
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(415, "Choose a PNG, JPEG or WebP avatar.")
    declared = request.headers.get("content-length")
    if declared is not None:
        if len(declared) > 12 or not declared.isascii() or not declared.isdecimal():
            raise HTTPException(400, "Invalid content length.")
        if int(declared) > MAX_BYTES:
            raise HTTPException(413, "Avatar must be no larger than 2 MiB.")
    if media.SLOTS.locked():
        raise HTTPException(429, "Upload capacity reached. Retry later.")
    async with media.SLOTS:
        extension = media.FORMATS[mime][0]
        descriptor, path = tempfile.mkstemp(prefix="blog-avatar-", suffix="." + extension)
        try:
            digest, size, head = hashlib.sha256(), 0, b""
            with os.fdopen(descriptor, "wb") as file:
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise HTTPException(413, "Avatar must be no larger than 2 MiB.")
                        digest.update(chunk)
                        head = (head + chunk[:32])[:32]
                        file.write(chunk)
            if not size or not media.signature(head, mime):
                raise HTTPException(422, "Invalid avatar image content.")
            await media.probe(path, mime)
            # Verified membership + content hash scopes retries without replacing old avatars.
            key = f"blogs/authors/{membership.id}/{digest.hexdigest()}.{extension}"
            async with storage.client() as client:
                with open(path, "rb") as file:
                    await asyncio.wait_for(
                        client.put_object(
                            Bucket=storage.bucket,
                            Key=key,
                            Body=file,
                            ContentLength=size,
                            ContentType=mime,
                            CacheControl="public, max-age=31536000, immutable",
                        ),
                        45,
                    )
            # Profile selection is committed only through the existing Save profile action.
            return {"url": storage.url(key)}
        finally:
            with suppress(OSError):
                os.unlink(path)
