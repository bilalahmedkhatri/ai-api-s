"""POST /api/v1/ingest/audio — Whisper speech-to-text ingestion."""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.api.v1.ingest.schemas import IngestAudioResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["ingest"])

ALLOWED_AUDIO = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".webm"}


@router.post("/", response_model=IngestAudioResponse, summary="Transcribe audio via Whisper")
async def ingest_audio(file: UploadFile, request: Request) -> IngestAudioResponse:
    """
    Accept an audio file (WAV / MP3 / OGG / M4A / FLAC / WEBM),
    run it through OpenAI Whisper (base model, CPU), and return the
    transcript tagged with input_type='voice' for the query pipeline.
    """
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio format '{suffix}'. Allowed: {sorted(ALLOWED_AUDIO)}",
        )

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Lazy import — Whisper is heavy; only load when the endpoint is actually called.
    try:
        import whisper  # noqa: PLC0415
    except ImportError as exc:
        logger.error("Whisper package not installed: %s", exc)
        raise HTTPException(status_code=500, detail="Whisper library is not installed on this server.") from exc

    # Write to a temp file; Whisper's load_audio() works on file paths.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        model = whisper.load_model("base")  # ~74 MB — fast on CPU
        result = whisper.transcribe(model, tmp_path)
    except Exception as exc:
        logger.error("Whisper transcription failed", exc_info=True,
                     extra={"request_id": getattr(request.state, "request_id", None)})
        raise HTTPException(status_code=500, detail=f"Transcription error: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    transcript: str = result.get("text", "").strip()
    logger.info("Audio transcribed", extra={
        "request_id": getattr(request.state, "request_id", None),
        "input_type": "voice",
        "language": result.get("language"),
    })

    return IngestAudioResponse(
        transcript=transcript,
        language=result.get("language"),
    )
