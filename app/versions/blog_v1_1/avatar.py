"""Bounded self-avatar upload using existing public B2 storage and image validation."""

import asyncio
import hashlib
import os
import tempfile
from contextlib import suppress
from urllib.parse import unquote, urlsplit

from app.blog import media
from app.blog.content import slug_base
from app.core.config import settings
from fastapi import HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer

MAX_BYTES = 2 * 1024 * 1024


async def upload_avatar(request, membership, storage):
    signer = serializer()
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
            key = f"blogs/authors/{membership.id}/{slug_base(membership.name, limit=80)}-{digest.hexdigest()}.{extension}"
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
            url = storage.url(key)
            return {
                "url": url,
                "upload_token": signer.dumps({"membership_id": membership.id, "url": url}),
            }
        finally:
            with suppress(OSError):
                os.unlink(path)


def serializer():
    if not settings.secret_key:
        raise HTTPException(503, "Upload receipt signing is not configured.")
    return URLSafeTimedSerializer(settings.secret_key, salt="blog-v1.1-avatar")


def verify_avatar(url, token, membership_id):
    try:
        receipt = serializer().loads(token or "", max_age=86400)
        if receipt != {"membership_id": membership_id, "url": url}:
            raise ValueError()
    except (BadSignature, ValueError, TypeError):
        raise HTTPException(
            422, "Upload a profile image and provide its valid upload token."
        ) from None


def trusted_avatar(url, membership_id, storage):
    base = storage.public_base + "/blogs/authors/" + str(membership_id) + "/"
    if not url or not url.startswith(base):
        return False
    name = unquote(url[len(base) :])
    import re

    return bool(re.fullmatch(r"[\w-]+-[0-9a-f]{64}\.(?:png|jpg|webp)", name)) and not (
        urlsplit(url).query or urlsplit(url).fragment
    )
