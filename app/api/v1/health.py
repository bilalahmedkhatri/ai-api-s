"""GET /api/v1/health — deep health check for cloud orchestrators.

Checks:
  - SQLite database connectivity (WAL mode read)
  - Upstash Redis reachability (if configured)
  - Application version / uptime metadata

Returns HTTP 200 when all critical dependencies are reachable,
HTTP 503 when the database is unreachable (non-negotiable dependency).
"""

import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["ops"])

_START_TIME = time.time()


class ComponentHealth(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    latency_ms: float | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    uptime_s: float
    components: dict[str, ComponentHealth]


async def _check_db(db: AsyncSession) -> ComponentHealth:
    t0 = time.monotonic()
    try:
        result = await db.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        return ComponentHealth(
            status="ok",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=f"journal_mode={mode}",
        )
    except Exception as exc:
        logger.error("Health: DB check failed: %s", exc)
        return ComponentHealth(status="unavailable", detail=str(exc))


async def _check_upstash() -> ComponentHealth:
    from app.core.cache import cache  # noqa: PLC0415

    if not cache.available:
        return ComponentHealth(status="degraded", detail="Upstash not configured")

    t0 = time.monotonic()
    try:
        await cache.set("__health__", "ping", ttl=5)
        val = await cache.get("__health__")
        ok = val == "ping"
        detail = "ping=ok" if ok else "Cache read/write verification failed (verify UPSTASH_REDIS_REST_TOKEN write permissions)"
        return ComponentHealth(
            status="ok" if ok else "degraded",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
            detail=detail,
        )
    except Exception as exc:
        return ComponentHealth(status="degraded", detail=str(exc))


@router.get("/", response_model=HealthResponse, summary="Deep health check")
async def deep_health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """
    Checks SQLite and Upstash connectivity.
    Returns 503 if the database is unavailable (critical dependency).
    Returns 200 with status='degraded' if Upstash is unreachable (non-critical).
    """
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    db_health = await _check_db(db)
    upstash_health = await _check_upstash()

    components = {"database": db_health, "cache": upstash_health}

    if db_health.status == "unavailable":
        overall = "unavailable"
    elif any(c.status == "degraded" for c in components.values()):
        overall = "degraded"
    else:
        overall = "ok"

    body = HealthResponse(
        status=overall,
        uptime_s=round(time.time() - _START_TIME, 1),
        components=components,
    )

    status_code = 503 if overall == "unavailable" else 200
    return JSONResponse(content=body.model_dump(), status_code=status_code)
