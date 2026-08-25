"""API v1 router — aggregates all sub-routers under /api/v1."""

from fastapi import APIRouter

from app.api.v1.cron import router as cron_router
from app.api.v1.health import router as health_router
from app.api.v1.ingest.audio import router as audio_router
from app.api.v1.ingest.image import router as image_router
from app.api.v1.ingest.video import router as video_router
from app.api.v1.keys.router import router as keys_router
from app.api.v1.query.router import router as query_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(health_router)
v1_router.include_router(cron_router)
v1_router.include_router(keys_router)
v1_router.include_router(audio_router, prefix="/ingest")
v1_router.include_router(image_router, prefix="/ingest")
v1_router.include_router(video_router, prefix="/ingest")
v1_router.include_router(query_router)
