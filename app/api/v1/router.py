"""API v1 router - aggregates all sub-routers under /api/v1."""

from fastapi import APIRouter, Depends

from app.api.v1.apps.router import router as apps_router
from app.api.v1.audio.router import router as audio_speech_router
from app.api.v1.auth.router import router as auth_router
from app.api.v1.cron import router as cron_router
from app.api.v1.dashboard.authorized_apps import router as authorized_apps_router
from app.api.v1.dashboard.customer_keys import router as customer_keys_router
from app.api.v1.dashboard.provider_models import router as provider_models_router
from app.api.v1.dashboard.router import router as dashboard_router
from app.api.v1.facebook.router import router as facebook_router
from app.api.v1.generate import router as generate_router
from app.api.v1.health import router as health_router
from app.api.v1.image.router import router as image_gen_router
from app.api.v1.ingest.image import router as image_router
from app.api.v1.ingest.video import router as video_router
from app.api.v1.keys.router import router as keys_router
from app.api.v1.media.router import router as media_router
from app.api.v1.query.router import router as query_router
from app.api.v1.video.router import router as video_gen_router
from app.blog.router import router as blog_router
from app.core.auth import get_current_api_key

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(health_router)
v1_router.include_router(cron_router)
v1_router.include_router(keys_router)
v1_router.include_router(apps_router)
app_dependencies = [Depends(get_current_api_key)]
v1_router.include_router(audio_speech_router, dependencies=app_dependencies)
v1_router.include_router(image_router, prefix="/ingest", dependencies=app_dependencies)
v1_router.include_router(video_router, prefix="/ingest", dependencies=app_dependencies)
v1_router.include_router(query_router, dependencies=app_dependencies)
v1_router.include_router(generate_router, dependencies=app_dependencies)
v1_router.include_router(auth_router)
v1_router.include_router(image_gen_router, dependencies=app_dependencies)
v1_router.include_router(video_gen_router, dependencies=app_dependencies)
v1_router.include_router(facebook_router, dependencies=app_dependencies)
v1_router.include_router(media_router, dependencies=app_dependencies)


v1_router.include_router(dashboard_router)
v1_router.include_router(customer_keys_router)

v1_router.include_router(provider_models_router)

v1_router.include_router(authorized_apps_router)

v1_router.include_router(blog_router)
