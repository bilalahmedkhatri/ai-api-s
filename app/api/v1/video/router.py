"""app/api/v1/video/router.py — Video generation endpoints."""

import asyncio
import logging
from datetime import UTC, datetime

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.video.schemas import VideoGenerateRequest, VideoGenerateResponse
from app.core.middleware import limiter
from app.db.database import get_db
from app.models.db_models import MediaGeneration
from app.services.generation_telemetry import finalize_media, media_metadata, media_telemetry
from app.services.media_gen_service import generate_media, get_media_models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/video", tags=["video"])


@router.get("/models", summary="List active video models")
async def list_video_models(db: AsyncSession = Depends(get_db)):
    """Return all active video models configured in the database."""
    models = await get_media_models(db, media_type="video")
    return {"models": models}


@router.post("/generate", response_model=VideoGenerateResponse, summary="Generate video")
@limiter.limit("3/minute")
async def generate_video_endpoint(
    request: Request,
    body: VideoGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a video using the specified model.
    Requires the central authorized-app check; audit ownership comes from the app.
    """
    user_id = request.state.authorized_app.owner_user_id

    from app.services.media_gen_service import get_media_model
    try:
        model = await get_media_model(body.model, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    if model.media_type != "video":
        raise HTTPException(status_code=400, detail=f"Model '{body.model}' is not a video model.")

    extra_params = body.extra_params or {}
    if body.aspect_ratio:
        extra_params["aspect_ratio"] = body.aspect_ratio
    if body.fps:
        extra_params["fps"] = body.fps

    # Create pending audit log
    audit = MediaGeneration(
        user_id=user_id,
        model_id=model.id,
        prompt=body.prompt,
        negative_prompt=body.negative_prompt,
        width=body.width or model.default_width,
        height=body.height or model.default_height,
        duration_s=body.duration_s,
        num_outputs=min(body.num_outputs, model.max_outputs),
        extra_params_json=media_metadata(request, request.state.authorized_app.id, model, extra_params),
        status="pending",
    )
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    start_time = datetime.now(UTC)
    try:
        # Generate!
        with media_telemetry(audit.id):
            urls, media_type = await generate_media(
                db=db, model_snapshot=model,
                model_name=body.model,
                prompt=body.prompt,
                negative_prompt=body.negative_prompt,
                width=body.width,
                height=body.height,
                duration_s=body.duration_s,
                num_outputs=body.num_outputs,
                extra_params=extra_params,
            )

        # Update audit on success
        import json
        audit.result_urls = json.dumps(urls)
        audit.status = "success"

    except asyncio.CancelledError:
        audit.status = "cancelled"
        audit.error_message = "CancelledError"
        raise

    except TimeoutError as e:
        audit.status = "timeout"
        audit.error_message = type(e).__name__
        logger.warning("Video generation timeout")
        raise HTTPException(status_code=504, detail="Generation timed out.") from e

    except Exception as e:
        audit.status = "failed"
        audit.error_message = type(e).__name__
        logger.warning("Video generation failed error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Generation failed. Please retry later.") from e

    finally:
        elapsed = (datetime.now(UTC) - start_time).total_seconds() * 1000
        audit.elapsed_ms = elapsed
        with CancelScope(shield=True):
            await finalize_media(db, audit)

    return VideoGenerateResponse(
        model=body.model,
        prompt=body.prompt,
        media_type=media_type,
        outputs=urls,
        elapsed_ms=elapsed,
        width=audit.width,
        height=audit.height,
        duration_s=audit.duration_s,
    )
