"""app/api/v1/audio/router.py — Dynamic Audio Text-to-Speech (TTS) endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.audio.schemas import DynamicTTSRequest
from app.core.auth import get_current_api_key
from app.models.db_models import AccessAPIKey
from app.db.database import get_db
from app.services.tts_service import (
    generate_dynamic_speech,
    get_dynamic_tts_models,
    get_dynamic_voices
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])


@router.get("/models", summary="List all active TTS models from the database")
async def list_models_endpoint(db: AsyncSession = Depends(get_db)):
    """Returns a list of all active TTS models registered in the database."""
    try:
        models = await get_dynamic_tts_models(db)
        return {"status": "success", "count": len(models), "models": models}
    except Exception as exc:
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
    summary="Generate speech audio dynamically using database configurations",
    response_class=Response,
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Returns raw WAV audio stream."}
    },
)
async def generate_tts_endpoint(
    req: DynamicTTSRequest,
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """
    Generate natural speech audio from text using any configured model in the database.
    Dynamically routes to Gemini, Kokoro Local, or Kokoro Replicate.
    """
    try:
        wav_bytes, mime_type = await generate_dynamic_speech(
            db=db,
            model_name=req.model,
            text=req.text,
            voice=req.voice,
            speed=req.speed,
            lang=req.lang,
            is_sample_request=False
        )
        return Response(
            content=wav_bytes,
            media_type=mime_type or "audio/wav",
            headers={
                "Content-Disposition": f"attachment; filename={req.model}_speech.wav",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        if "GEMINI_QUOTA_EXCEEDED" in str(exc):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Gemini API rate limit or quota exceeded.") from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error generating dynamic TTS audio: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TTS generation failed: {str(exc)}"
        ) from exc
