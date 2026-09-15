"""app/api/v1/video/router.py — Video generation endpoints."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.video.schemas import VideoGenerateRequest, VideoGenerateResponse
from app.core.auth import get_current_api_key
from app.core.user_auth import get_optional_user
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, MediaGeneration, User
from app.services.media_gen_service import generate_media, get_media_models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/video", tags=["video"])


@router.get("/models", summary="List active video models")
async def list_video_models(db: AsyncSession = Depends(get_db)):
    """Return all active video models configured in the database."""
    models = await get_media_models(db, media_type="video")
    return {"models": models}


from app.core.middleware import limiter

@router.post("/generate", response_model=VideoGenerateResponse, summary="Generate video")
@limiter.limit("3/minute")
async def generate_video_endpoint(
    request: Request,
    body: VideoGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
):
    """
    Generate a video using the specified model.
    Requires either a valid session cookie OR a valid X-API-Key.
    """
    if not user and not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Use a session cookie or provide an X-API-Key header.",
        )

    user_id = user.id if user else None
    
    from app.services.media_gen_service import get_media_model
    try:
        model = await get_media_model(body.model, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
        
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
        extra_params_json=extra_params,
        status="pending",
    )
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    start_time = datetime.now(UTC)
    try:
        # Generate!
        urls, media_type = await generate_media(
            db=db,
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
        
    except TimeoutError as e:
        audit.status = "timeout"
        audit.error_message = str(e)
        logger.error(f"Video generation timeout: {e}")
        await db.commit()
        raise HTTPException(status_code=504, detail="Generation timed out.")
        
    except Exception as e:
        audit.status = "failed"
        audit.error_message = str(e)
        logger.error("Video generation failed", exc_info=True)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    finally:
        elapsed = (datetime.now(UTC) - start_time).total_seconds() * 1000
        audit.elapsed_ms = elapsed
        await db.commit()

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
