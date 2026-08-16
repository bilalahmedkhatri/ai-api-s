"""POST /api/v1/ingest/video — Keyframe extraction + vision summarisation.

Ponytail note: Video-LLaMA / LLaVA-NeXT were spec'd but rejected (Step 1+5).
Keyframe extraction via ffmpeg is done via stdlib subprocess if ffmpeg is on PATH;
each keyframe is then described by the same litellm vision call used in /ingest/image.
No new dependencies are added — ffmpeg is an optional system binary.
"""

import base64
import logging
import subprocess
import tempfile
from pathlib import Path

import litellm
from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.api.v1.ingest.schemas import IngestVideoResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video", tags=["ingest"])

ALLOWED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_MAX_KEYFRAMES = 6  # keep cost and latency bounded


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _extract_keyframes(video_path: str, out_dir: str, max_frames: int) -> list[Path]:
    """Use ffmpeg to extract up to max_frames evenly-spaced keyframes as JPEG."""
    subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vf", "select='not(mod(n,30))',scale=512:-1",  # every 30th frame
            "-vframes", str(max_frames),
            "-q:v", "2",
            f"{out_dir}/frame_%03d.jpg",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return sorted(Path(out_dir).glob("frame_*.jpg"))


def _describe_frame(frame_path: Path) -> str:
    b64 = base64.b64encode(frame_path.read_bytes()).decode()
    from app.services.model_router import call_vision_llm
    desc, _ = call_vision_llm(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Briefly describe what is happening in this video frame (1-2 sentences)."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        max_tokens=128,
    )
    return desc


@router.post("/", response_model=IngestVideoResponse, summary="Summarise video via keyframe analysis")
async def ingest_video(file: UploadFile, request: Request) -> IngestVideoResponse:
    """
    Accept a video file, extract keyframes with ffmpeg (must be on PATH),
    describe each frame via a vision LLM, and return a chronological summary.

    Returns HTTP 501 if ffmpeg is not available on the system.
    """
    if not _ffmpeg_available():
        raise HTTPException(
            status_code=501,
            detail="ffmpeg is not installed or not on PATH. Install ffmpeg to enable video ingestion.",
        )

    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in ALLOWED_VIDEO:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported video format '{suffix}'. Allowed: {sorted(ALLOWED_VIDEO)}",
        )

    video_bytes = await file.read()
    if not video_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")



    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / f"input{suffix}"
        video_path.write_bytes(video_bytes)

        try:
            frames = _extract_keyframes(str(video_path), tmpdir, _MAX_KEYFRAMES)
        except subprocess.CalledProcessError as exc:
            logger.error("ffmpeg keyframe extraction failed", exc_info=True)
            raise HTTPException(status_code=500, detail=f"ffmpeg error: {exc.stderr.decode()[:200]}") from exc

        if not frames:
            raise HTTPException(status_code=422, detail="No keyframes could be extracted from the video.")

        descriptions: list[str] = []
        for i, frame in enumerate(frames, 1):
            try:
                desc = _describe_frame(frame)
                descriptions.append(f"[Frame {i}] {desc}")
            except Exception as exc:
                logger.warning("Frame %d description failed: %s", i, exc)
                descriptions.append(f"[Frame {i}] (description unavailable)")

    summary = " ".join(descriptions)
    logger.info("Video processed", extra={
        "request_id": getattr(request.state, "request_id", None),
        "input_type": "video",
        "keyframes": len(descriptions),
        "file_name": file.filename,
    })

    return IngestVideoResponse(
        keyframes_extracted=len(descriptions),
        summary=summary,
        filename=file.filename or "upload",
    )
