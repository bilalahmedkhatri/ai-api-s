"""app/api/v1/audio/router.py — Dynamic Audio Text-to-Speech (TTS) endpoints."""

import logging
import socket

from app.api.v1.audio.schemas import DynamicTTSRequest, RenewAudioUrlRequest
from app.core.auth import get_current_api_key
from app.db.database import get_db
from app.models.db_models import AccessAPIKey
from app.services.tts_service import (
    generate_dynamic_speech,
    get_dynamic_tts_models,
    get_dynamic_voices,
)
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _is_db_connectivity_error(exc: Exception) -> bool:
    """True when the root cause is a network/DNS failure reaching the DB host."""
    msg = str(exc).lower()
    return (
        isinstance(exc, socket.gaierror)
        or "getaddrinfo" in msg
        or "connectiondoesnotexist" in msg
        or "connection was closed" in msg
        or "connection refused" in msg
    )

router = APIRouter(prefix="/audio", tags=["audio"])


@router.get("/models", summary="List all active TTS models from the database")
async def list_models_endpoint(db: AsyncSession = Depends(get_db)):
    """Returns a list of all active TTS models registered in the database."""
    try:
        models = await get_dynamic_tts_models(db)
        return {"status": "success", "count": len(models), "models": models}
    except Exception as exc:
        if _is_db_connectivity_error(exc):
            logger.warning("DB unreachable on /models: %s", exc)
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "30"},
                content={"detail": "Database temporarily unavailable. Please retry in a few seconds."},
            )
        logger.error("Error retrieving models: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve models: {str(exc)}"
        ) from exc


from fastapi import Query


@router.get("/voices", summary="List voices for a specific model")
async def list_voices_endpoint(
    model: str,
    search: str | None = Query(None, description="Search voice by name or gender"),
    limit: int = Query(10, ge=1, le=100, description="Number of voices to return"),
    offset: int = Query(0, ge=0, description="Number of voices to skip"),
    db: AsyncSession = Depends(get_db)
):
    """Returns detailed list of voices and sample links for the requested model."""
    try:
        voices, total_count = await get_dynamic_voices(model, db, search, limit, offset)
        return {
            "status": "success",
            "model": model,
            "count": len(voices),
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "voices": voices
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        if _is_db_connectivity_error(exc):
            logger.warning("DB unreachable on /voices: %s", exc)
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "30"},
                content={"detail": "Database temporarily unavailable. Please retry in a few seconds."},
            )
        logger.error("Error retrieving voices: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve voices: {str(exc)}"
        ) from exc


@router.get(
    "/sample",
    summary="Get audio sample preview for a specific voice and model",
    response_class=Response,
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Returns raw WAV audio preview stream."}
    },
)
async def get_sample_endpoint(
    model: str,
    voice: str,
    text: str | None = None,
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Generate or stream a voice audio sample preview locally or via API if missing."""
    try:
        # Pass a fallback text if not provided, though the DB sample_text is ideally fetched inside
        sample_text = text or f"This is a sample text for {voice}."

        wav_bytes, mime_type = await generate_dynamic_speech(
            db=db,
            model_name=model,
            text=sample_text,
            voice=voice,
            is_sample_request=True
        )
        return Response(
            content=wav_bytes,
            media_type=mime_type or "audio/wav",
            headers={
                "Content-Disposition": f"inline; filename=sample_{model}_{voice}.wav",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        if "GEMINI_QUOTA_EXCEEDED" in str(exc):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Gemini API rate limit or quota exceeded.") from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error generating sample: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate sample: {str(exc)}"
        ) from exc


import uuid

from fastapi import BackgroundTasks


@router.post(
    "/tts",
    summary="Generate speech audio dynamically using background processing",
    responses={
        200: {"description": "Returns processing status and job_id"}
    },
)
async def generate_tts_endpoint(
    req: DynamicTTSRequest,
    background_tasks: BackgroundTasks,
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate natural speech audio from text. Runs asynchronously in the background.
    Returns a job_id which can be polled for status.
    """
    from app.models.db_models import TTSJob, TTSModel
    from sqlalchemy import select

    # 1. Validate model exists and is active in DB
    stmt = select(TTSModel).where(TTSModel.name == req.model, TTSModel.is_active == True)
    if req.provider:
        stmt = stmt.where(TTSModel.provider.ilike(req.provider.strip()))
    model_obj = (await db.execute(stmt)).scalar_one_or_none()
    if not model_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{req.model}' not found"
        )

    try:
        job_id = str(uuid.uuid4())

        # Save pending job to DB
        job_title = req.title or req.text[:100]
        new_job = TTSJob(
            job_id=job_id,
            title=job_title,
            text=req.text,
            status="processing",
            audio_url=None
        )
        db.add(new_job)
        await db.commit()

        # Trigger background processing
        from app.services.tts_service import process_tts_job
        background_tasks.add_task(
            process_tts_job,
            job_id=job_id,
            model_name=req.model,
            text=req.text,
            voice=req.voice,
            lang=req.lang,
            extra_params=req.extra_params,
            provider=model_obj.provider,
        )

        return {
            "job_id": job_id,
            "title": job_title,
            "status": "processing",
            "audio_url": None
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error creating TTS job: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start TTS job: {str(exc)}"
        ) from exc


@router.get(
    "/tts/status/{job_id}",
    summary="Check status of an asynchronous TTS job",
)
async def get_tts_status_endpoint(
    job_id: str,
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Check the status of a background TTS job using its job_id.
    """
    from app.models.db_models import TTSJob
    from sqlalchemy import select

    result = await db.execute(select(TTSJob).where(TTSJob.job_id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.services.voice_url_service import ensure_valid_audio_url
    valid_audio_url = await ensure_valid_audio_url(job, db) if job.status == "completed" else job.audio_url

    return {
        "job_id": job.job_id,
        "title": job.title,
        "status": job.status,
        "audio_url": valid_audio_url,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.post(
    "/tts/renew-url",
    summary="Renew an expired TTS audio pre-signed URL for 7 days",
)
async def renew_tts_audio_url_endpoint(
    req: RenewAudioUrlRequest,
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Renew an expired 7-day Backblaze S3 pre-signed URL.
    Pass either 'job_id' or 'url' (or both).
    Generates a fresh 7-day pre-signed URL and updates the database record.
    """
    from app.services.voice_url_service import renew_tts_audio_url

    if not req.job_id and not req.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'job_id' or 'url' must be provided."
        )

    try:
        result = await renew_tts_audio_url(db=db, job_id=req.job_id, url=req.url)
        return result
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
    except Exception as exc:
        logger.error("Error renewing audio URL: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to renew audio URL: {str(exc)}"
        ) from exc
