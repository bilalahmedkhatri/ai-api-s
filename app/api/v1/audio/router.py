"""app/api/v1/audio/router.py — Dynamic Audio Text-to-Speech (TTS) endpoints."""

import json
import logging
import socket
import uuid
from dataclasses import asdict

from app.api.v1.audio.schemas import DynamicTTSRequest, RenewAudioUrlRequest
from app.core.auth import get_current_api_key
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, TTSChunk, TTSJob
from app.services.generation_telemetry import request_identity
from app.services.tts.availability import LOCAL_KOKORO_UNAVAILABLE, is_local_kokoro
from app.services.tts.constants import GEMINI_CHUNK_MAX_CHARS, KOKORO_CHUNK_MAX_CHARS
from app.services.tts.generator import _ModelInfo
from app.services.tts.jobs import _now, lease_deadline
from app.services.tts.text_utils import split_text_into_chunks
from app.services.tts_service import (
    generate_dynamic_speech,
    get_dynamic_tts_models,
    get_dynamic_voices,
)
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import select
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




@router.post(
    "/tts",
    summary="Generate speech audio dynamically using background processing",
    responses={
        200: {"description": "Returns processing status and job_id"}
    },
)
async def generate_tts_endpoint(
    req: DynamicTTSRequest,
    request: Request,
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
    stmt = select(TTSModel).where(TTSModel.name == req.model)
    if req.provider:
        stmt = stmt.where(TTSModel.provider.ilike(req.provider.strip()))
    model_obj = (await db.execute(stmt)).scalar_one_or_none()
    from app.services.model_endpoints import legacy_views
    models = await legacy_views(db, [model_obj] if model_obj else [], "tts")
    model_obj = models[0] if models else None
    if not model_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{req.model}' not found"
        )

    if is_local_kokoro(model_obj.provider, model_obj.is_local):
        raise HTTPException(400, LOCAL_KOKORO_UNAVAILABLE)

    provider_name = model_obj.provider.lower()
    if provider_name not in {'gemini', 'kokoro'}:
        raise HTTPException(400, "Unsupported TTS provider")
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")
    app_id = _app_id(api_key)
    try:
        from app.services.tts.submission import submission_identity
        job_id, submission_fingerprint, existing = await submission_identity(db, request, req, app_id)
        if existing:
            return {"job_id": existing.job_id, "title": existing.title, "status": "processing", "audio_url": None}
        token = uuid.uuid4().hex
        snapshot = asdict(_ModelInfo(
            name=model_obj.name, provider=model_obj.provider, is_local=bool(model_obj.is_local),
            api_endpoint_url=model_obj.api_endpoint_url, provider_config=model_obj.provider_config or {},
            default_voice=model_obj.default_voice, default_speed=model_obj.default_speed,
            default_lang=model_obj.default_lang,
            model_endpoint_id=getattr(model_obj, "model_endpoint_id", None),
        ))
        generation = {"submission_fingerprint": submission_fingerprint, "model_snapshot": snapshot, "voice": req.voice or model_obj.default_voice,
                      "lang": req.lang, "speed": req.speed, "extra_params": req.extra_params,
                      "_telemetry": {"version": 1, "coverage_complete": True, "attempts": [],
                                     **request_identity(request, app_id)}}
        limit = GEMINI_CHUNK_MAX_CHARS if provider_name == 'gemini' else KOKORO_CHUNK_MAX_CHARS

        # Save pending job to DB
        job_title = req.title or req.text[:100]
        new_job = TTSJob(
            job_id=job_id,
            title=job_title,
            text=req.text,
            status="processing",
            audio_url=None, authorized_app_id=app_id, generation_json=json.dumps(generation),
            run_token=token, lease_until=lease_deadline(),
        )
        db.add(new_job)
        await db.flush()
        for index, chunk in enumerate(split_text_into_chunks(req.text, limit)):
            db.add(TTSChunk(job_id=job_id, chunk_index=index, text=chunk, status="pending", attempts=0))
        await db.commit()

        # Trigger background processing
        from app.services.tts_service import process_tts_job
        background_tasks.add_task(
            process_tts_job,
            job_id=job_id,
            run_token=token,
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

    result = await db.execute(select(TTSJob).where(TTSJob.job_id == job_id, TTSJob.authorized_app_id == _app_id(api_key)))
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
        "duration_seconds": job.duration_sec,
        "size_bytes": job.size_bytes,
        "generation_time_sec": round((job.updated_at - job.created_at).total_seconds(), 1)
        if job.status == 'completed' and job.updated_at and job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "error": job.error_message,
        "chunks": await _chunk_status(db, job_id),
        "retry_available": _retryable(job),
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
        result = await renew_tts_audio_url(db=db, job_id=req.job_id, url=req.url, authorized_app_id=_app_id(api_key))
        return result
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
    except Exception as exc:
        logger.error("Error renewing audio URL: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to renew audio URL: {str(exc)}"
        ) from exc


def _app_id(api_key):
    app_id = getattr(api_key, 'authorized_app_id', None)
    if not app_id:
        raise HTTPException(403, "App ownership is required")
    return app_id


def _retryable(job):
    if not job.generation_json or job.status == 'completed':
        return False
    if job.status == 'failed' or job.lease_until is None:
        return True
    deadline = job.lease_until
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=_now().tzinfo)
    return deadline < _now()


async def _chunk_status(db, job_id):
    rows = (await db.execute(select(TTSChunk).where(TTSChunk.job_id == job_id)
                            .order_by(TTSChunk.chunk_index))).scalars().all()
    return {"total": len(rows), "completed": sum(c.status == 'completed' for c in rows),
            "failed": sum(c.status == 'failed' for c in rows),
            "items": [{"index": c.chunk_index, "status": c.status,
                       "attempts": c.attempts, "error": c.error_message} for c in rows]}


@router.post('/tts/retry/{job_id}', summary="Resume a failed or abandoned app-owned job", status_code=202)
async def retry_tts_endpoint(job_id: str, background_tasks: BackgroundTasks,
                             api_key: AccessAPIKey = Depends(get_current_api_key),
                             db: AsyncSession = Depends(get_db)):
    app_id = _app_id(api_key)
    job = (await db.execute(select(TTSJob).where(
        TTSJob.job_id == job_id, TTSJob.authorized_app_id == app_id,
    ))).scalar_one_or_none()
    if not job:
        raise HTTPException(404, 'Job not found')
    if not _retryable(job):
        raise HTTPException(409, 'Job is completed, still running, or has no retry settings')
    snapshot = json.loads(job.generation_json).get('model_snapshot', {})
    if is_local_kokoro(snapshot.get('provider'), snapshot.get('is_local')):
        raise HTTPException(400, LOCAL_KOKORO_UNAVAILABLE)
    from app.services.tts.jobs import claim_retry
    token = await claim_retry(db, job_id, app_id)
    if not token:
        raise HTTPException(409, 'Another worker already claimed this job')
    from app.services.tts_service import process_tts_job
    background_tasks.add_task(process_tts_job, job_id=job_id, run_token=token)
    return {"job_id": job_id, "status": "processing", "audio_url": None}


@router.get('/tts/stream/{job_id}', summary="Listen to indexed chunks as they become ready")
async def stream_tts_endpoint(job_id: str, after: int = Query(0, ge=0),
                              api_key: AccessAPIKey = Depends(get_current_api_key),
                              db: AsyncSession = Depends(get_db)):
    from app.services.tts.streaming import stream_chunks
    from fastapi.responses import StreamingResponse
    app_id = _app_id(api_key)
    job = (await db.execute(select(TTSJob).where(
        TTSJob.job_id == job_id, TTSJob.authorized_app_id == app_id,
    ))).scalar_one_or_none()
    if not job:
        raise HTTPException(404, 'Job not found')
    # Release the auth/lookup transaction before the long response starts.
    await db.commit()
    return StreamingResponse(stream_chunks(job_id, app_id, after), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'private, no-store', 'X-Accel-Buffering': 'no'})
