"""POST /api/v1/ingest/audio — Whisper speech-to-text ingestion (file upload or URL)."""

import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import torch
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.api.v1.ingest.schemas import AudioSegment, IngestAudioResponse, IngestAudioUrlRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["ingest"])

ALLOWED_AUDIO = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".webm", ".aac", ".wma"}
ALLOWED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v"}
ALLOWED_MEDIA = ALLOWED_AUDIO | ALLOWED_VIDEO

MAX_DURATION_SECONDS = 25 * 60  # 25 minutes (1500 seconds)
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB safe limit

STREAMING_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "reddit.com",
    "twitch.tv",
    "soundcloud.com",
)

MIME_TO_SUFFIX = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/aac": ".aac",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}


def _is_streaming_platform_url(url: str) -> bool:
    """Check if the URL belongs to a streaming or video sharing platform."""
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in STREAMING_DOMAINS)


def _get_media_duration(target: str, timeout: int = 15) -> float | None:
    """Probe duration of local file path or remote HTTP/HTTPS URL via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        target,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
        val = proc.stdout.strip()
        if val:
            return float(val)
    except Exception as exc:
        logger.debug("ffprobe failed to determine duration for '%s': %s", target, exc)
        return None
    return None


def _download_from_streaming_platform(url: str, request_id: str | None = None) -> tuple[str, float | None]:
    """
    Extract audio stream from YouTube / streaming video platforms using yt-dlp.
    Validates duration before downloading the stream.
    Returns (downloaded_file_path, duration_seconds).
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as exc:
        logger.error("yt-dlp package not installed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="yt-dlp is required to process streaming platform URLs but is not installed.",
        ) from exc

    # 1. Probe metadata first without downloading
    ydl_opts_meta = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.error("Failed to retrieve platform media info for '%s': %s", url, exc, extra={"request_id": request_id})
        raise HTTPException(
            status_code=400,
            detail=f"Could not retrieve video information from URL: {exc}",
        ) from exc

    if not info:
        raise HTTPException(status_code=400, detail="Could not extract video metadata from URL.")

    duration = info.get("duration")
    if duration is not None and duration > MAX_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Media duration ({duration / 60:.1f} minutes) exceeds the "
                f"maximum allowed limit of 25 minutes ({MAX_DURATION_SECONDS // 60} minutes)."
            ),
        )

    # 2. Download audio stream only (lightweight & fast)
    out_template = os.path.join(tempfile.gettempdir(), f"stream_{uuid.uuid4().hex[:8]}_%(id)s.%(ext)s")
    ydl_opts_download = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": out_template,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            info_download = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info_download)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to download audio from platform URL '%s': %s", url, exc, extra={"request_id": request_id})
        raise HTTPException(status_code=400, detail=f"Error downloading stream audio: {exc}") from exc

    # Verify file exists on disk
    if not os.path.exists(downloaded_file):
        base_path = Path(downloaded_file).with_suffix("")
        candidates = list(Path(tempfile.gettempdir()).glob(f"{base_path.name}.*"))
        if candidates:
            downloaded_file = str(candidates[0])
        else:
            raise HTTPException(status_code=500, detail="Downloaded media file could not be located on disk.")

    # 3. Post-download duration check
    if duration is None:
        duration = _get_media_duration(downloaded_file)

    if duration is not None and duration > MAX_DURATION_SECONDS:
        Path(downloaded_file).unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Media duration ({duration / 60:.1f} minutes) exceeds the "
                f"maximum allowed limit of 25 minutes ({MAX_DURATION_SECONDS // 60} minutes)."
            ),
        )

    return downloaded_file, duration


async def _download_media_from_url(url: str, request_id: str | None = None) -> tuple[str, float | None]:
    """
    Check duration pre-download, download media to a temp file, and verify duration post-download.
    Returns (temp_file_path, duration_seconds).
    """
    # 0. Route streaming platforms (YouTube, Vimeo, etc.) to yt-dlp
    if _is_streaming_platform_url(url):
        return _download_from_streaming_platform(url, request_id=request_id)

    # 1. Pre-check: probe duration directly on the remote URL via ffprobe over HTTP/HTTPS
    remote_duration = _get_media_duration(url)
    if remote_duration is not None and remote_duration > MAX_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Media duration ({remote_duration / 60:.1f} minutes) exceeds the "
                f"maximum allowed limit of 25 minutes ({MAX_DURATION_SECONDS // 60} minutes)."
            ),
        )

    # 2. Determine file suffix
    parsed_path = urlparse(url).path
    suffix = Path(parsed_path).suffix.lower()

    # 3. Stream download using httpx
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=10.0),
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to download media from URL (HTTP status {resp.status_code}).",
                    )

                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                # If server returned HTML, attempt extraction via yt-dlp
                if "text/html" in content_type:
                    logger.info("URL '%s' returned HTML; attempting extraction via yt-dlp", url)
                    return _download_from_streaming_platform(url, request_id=request_id)

                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Media file size ({int(content_length) / (1024 * 1024):.1f} MB) exceeds maximum allowed limit of {MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB.",
                    )

                if not suffix or suffix not in ALLOWED_MEDIA:
                    suffix = MIME_TO_SUFFIX.get(content_type, suffix)

                if suffix not in ALLOWED_MEDIA:
                    raise HTTPException(
                        status_code=415,
                        detail=f"Unsupported media format '{suffix}'. Allowed formats: {sorted(ALLOWED_MEDIA)}",
                    )

                total_bytes = 0
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name
                    try:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            total_bytes += len(chunk)
                            if total_bytes > MAX_FILE_SIZE_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"Media file size exceeded {MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB limit during download.",
                                )
                            tmp.write(chunk)
                    except Exception:
                        Path(tmp_path).unlink(missing_ok=True)
                        raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error downloading media from URL '%s': %s", url, exc, extra={"request_id": request_id})
        raise HTTPException(status_code=400, detail=f"Could not download media from URL: {exc}") from exc

    # 4. Post-download duration check (fallback if remote probe was None)
    duration = remote_duration
    if duration is None:
        duration = _get_media_duration(tmp_path)

    if duration is not None and duration > MAX_DURATION_SECONDS:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Media duration ({duration / 60:.1f} minutes) exceeds the "
                f"maximum allowed limit of 25 minutes ({MAX_DURATION_SECONDS // 60} minutes)."
            ),
        )

    return tmp_path, duration


def _transcribe_file(
    tmp_path: str,
    language: str | None = None,
    request_id: str | None = None,
) -> tuple[str, str | None, list[AudioSegment]]:
    """Execute Whisper transcription on local media file path."""
    try:
        import whisper  # noqa: PLC0415
    except ImportError as exc:
        logger.error("Whisper package not installed: %s", exc)
        raise HTTPException(status_code=500, detail="Whisper library is not installed on this server.") from exc

    try:
        model = whisper.load_model("medium")
        transcribe_kwargs = {}
        if language:
            transcribe_kwargs["language"] = language
        result = whisper.transcribe(model, tmp_path, fp16=torch.cuda.is_available(), **transcribe_kwargs)
    except Exception as exc:
        logger.error("Whisper transcription failed", exc_info=True, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=f"Transcription error: {exc}") from exc

    transcript: str = result.get("text", "").strip()
    detected_lang: str | None = result.get("language")

    raw_segments = result.get("segments", []) or []
    segments = []
    for idx, seg in enumerate(raw_segments):
        start = round(float(seg.get("start", 0.0)), 2)
        end = round(float(seg.get("end", 0.0)), 2)
        duration = round(max(0.0, end - start), 2)
        segments.append(
            AudioSegment(
                id=int(seg.get("id", idx)),
                start=start,
                end=end,
                duration=duration,
                text=str(seg.get("text", "")).strip(),
            )
        )

    return transcript, detected_lang, segments


@router.post("/", response_model=IngestAudioResponse, summary="Transcribe audio or video via Whisper")
async def ingest_audio(
    file: UploadFile | None = File(None, description="Audio or video file upload"),
    url: str | None = Form(None, description="Direct media URL or streaming platform link (e.g. YouTube, Vimeo)"),
    language: str | None = Form(None, description="Optional 2-character ISO language code (e.g. 'ur', 'en', 'hi', 'fa', 'pa', 'ru')"),
    request: Request = None,  # type: ignore
) -> IngestAudioResponse:
    """
    Accept an audio/video file upload OR a public media URL link,
    optional 2-character language hint (e.g. 'ur', 'en', 'hi'),
    verify that duration does not exceed 25 minutes, extract speech via Whisper,
    and return the transcript tagged with input_type='voice'.
    """
    req_id = getattr(request.state, "request_id", None) if request and hasattr(request, "state") else None

    clean_language: str | None = None
    if language and isinstance(language, str) and language.strip():
        clean_language = language.strip().lower()

    # Support JSON payload if url/language was not sent as Form
    if request:
        if "application/json" in request.headers.get("content-type", ""):
            try:
                body = await request.json()
                if isinstance(body, dict):
                    if not url:
                        url = body.get("url")
                    if not clean_language and body.get("language"):
                        clean_language = str(body["language"]).strip().lower()
            except Exception:
                pass

    if clean_language:
        if len(clean_language) != 2:
            raise HTTPException(
                status_code=400,
                detail=f"Language code must be a 2-character ISO code (e.g. 'ur', 'en', 'hi'). Received: '{clean_language}'",
            )

    has_file = hasattr(file, "filename") and bool(file.filename)
    if not has_file and not url:
        raise HTTPException(
            status_code=400,
            detail="Either a media 'file' upload or a 'url' link must be provided.",
        )

    tmp_path: str | None = None
    duration_s: float | None = None

    try:
        if has_file and file is not None:
            suffix = Path(file.filename or "upload").suffix.lower()
            if suffix not in ALLOWED_MEDIA:
                raise HTTPException(
                    status_code=415,
                    detail=f"Unsupported media format '{suffix}'. Allowed formats: {sorted(ALLOWED_MEDIA)}",
                )

            audio_bytes = await file.read()
            if not audio_bytes:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            # Validate duration for uploaded file
            duration_s = _get_media_duration(tmp_path)
            if duration_s is not None and duration_s > MAX_DURATION_SECONDS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Media duration ({duration_s / 60:.1f} minutes) exceeds the "
                        f"maximum allowed limit of 25 minutes ({MAX_DURATION_SECONDS // 60} minutes)."
                    ),
                )
        elif url:
            tmp_path, duration_s = await _download_media_from_url(url.strip(), request_id=req_id)
        else:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        transcript, detected_lang, segments = _transcribe_file(tmp_path, language=clean_language, request_id=req_id)

    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    logger.info("Media transcribed", extra={
        "request_id": req_id,
        "input_type": "voice",
        "language": detected_lang,
    })

    return IngestAudioResponse(
        transcript=transcript,
        language=detected_lang,
        duration_s=round(duration_s, 2) if duration_s is not None else None,
        segments=segments,
    )


@router.post("/url", response_model=IngestAudioResponse, summary="Transcribe audio or video from URL via Whisper (JSON)")
async def ingest_audio_url(payload: IngestAudioUrlRequest, request: Request) -> IngestAudioResponse:
    """Transcribe media directly from a URL payload."""
    return await ingest_audio(file=None, url=payload.url, language=payload.language, request=request)
