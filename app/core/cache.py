"""Upstash Redis async client — thin wrapper with zlib compression.

Upstash provides serverless HTTP-based Redis, optimal for FastAPI and
cloud environments (no persistent TCP socket needed).

Usage:
    from app.core.cache import cache
    await cache.get("key")
    await cache.set("key", value, ttl=3600)

Requires in .env:
    UPSTASH_REDIS_REST_URL=https://...upstash.io
    UPSTASH_REDIS_REST_TOKEN=...

If credentials are missing, all cache calls are no-ops (fail-open).
"""

import json
import logging
import zlib
from typing import Any

logger = logging.getLogger(__name__)

# ── TTL constants ──────────────────────────────────────────────────────────────
TTL_STATIC = 60 * 60 * 24 * 7   # 7 days  — factual / stable answers
TTL_DYNAMIC = 60 * 60            # 1 hour  — real-time search results

# Compression threshold: only compress payloads > 512 bytes
_COMPRESS_THRESHOLD = 512
_COMPRESS_PREFIX = b"\x1f\x8b"  # sentinel to detect compressed payloads


def _compress(data: str) -> bytes:
    raw = data.encode()
    if len(raw) < _COMPRESS_THRESHOLD:
        return raw
    return _COMPRESS_PREFIX + zlib.compress(raw, level=6)


def _decompress(data: bytes) -> str:
    if data[:2] == _COMPRESS_PREFIX:
        return zlib.decompress(data[2:]).decode()
    return data.decode()


class _NoOpCache:
    """Used when Upstash credentials are absent — all methods are no-ops."""

    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any, ttl: int = TTL_STATIC) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass

    @property
    def available(self) -> bool:
        return False


class _UpstashCache:
    """Async Upstash Redis client with transparent zlib compression."""

    def __init__(self) -> None:
        from upstash_redis.asyncio import Redis  # lazy import

        self._redis = Redis.from_env()

    async def get(self, key: str) -> Any:
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            # Upstash returns strings; encode back to bytes for decompress check.
            data = raw.encode() if isinstance(raw, str) else raw
            payload = json.loads(_decompress(data))
            logger.debug("Cache HIT key=%s", key[:40])
            return payload
        except Exception as exc:
            logger.warning("Cache GET error (key=%s): %s", key[:40], exc)
            return None

    async def set(self, key: str, value: Any, ttl: int = TTL_STATIC) -> None:
        try:
            blob = _compress(json.dumps(value, ensure_ascii=False))
            # Upstash set() accepts bytes or str; pass as str via latin-1 safe path.
            await self._redis.set(key, blob.decode("latin-1"), ex=ttl)
            logger.debug("Cache SET key=%s ttl=%ds len=%d", key[:40], ttl, len(blob))
        except Exception as exc:
            logger.warning("Cache SET error (key=%s): %s", key[:40], exc)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as exc:
            logger.warning("Cache DEL error (key=%s): %s", key[:40], exc)

    @property
    def available(self) -> bool:
        return True


def _build_cache() -> _UpstashCache | _NoOpCache:
    import os

    has_url = bool(os.environ.get("UPSTASH_REDIS_REST_URL"))
    has_token = bool(os.environ.get("UPSTASH_REDIS_REST_TOKEN"))
    if has_url and has_token:
        logger.info("Upstash Redis cache enabled")
        return _UpstashCache()
    logger.info("Upstash credentials not set — cache disabled (fail-open)")
    return _NoOpCache()


# Module-level singleton — imported everywhere as `from app.core.cache import cache`
cache = _build_cache()
