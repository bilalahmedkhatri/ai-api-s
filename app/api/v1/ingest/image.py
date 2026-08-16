"""POST /api/v1/ingest/image — Vision description via litellm multimodal.

Ponytail note: LLaVA / BLIP-2 / Qwen-VL were spec'd but rejected (Step 5 —
not installed, require multi-GB downloads + GPU). litellm is already
installed and routes vision calls to GPT-4o / Gemini Vision via API keys
already managed by the app settings.
"""

import base64
import logging
from pathlib import Path

import litellm
from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.api.v1.ingest.schemas import IngestImageResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/image", tags=["ingest"])

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp"}




def _image_prompt(b64: str, mime: str) -> list[dict]:
    """Build a litellm-compatible multimodal message."""
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Describe this image in detail. Include: visual entities and objects, "
                        "any text visible via OCR, colors, spatial layout, and general scene context."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        }
    ]


@router.post("/", response_model=IngestImageResponse, summary="Describe an image via vision model")
async def ingest_image(file: UploadFile, request: Request) -> IngestImageResponse:
    """
    Accept a JPEG / PNG / GIF / WebP image, pass it to a vision-capable LLM
    via litellm, and return a detailed text description for the query pipeline.
    """
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in ALLOWED_IMAGE:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image format '{suffix}'. Allowed: {sorted(ALLOWED_IMAGE)}",
        )

    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}
    mime = mime_map[suffix]

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    b64 = base64.b64encode(image_bytes).decode()

    try:
        from app.services.model_router import call_vision_llm
        description, model_used = call_vision_llm(messages=_image_prompt(b64, mime), max_tokens=512)
    except Exception as exc:
        logger.error("Vision inference failed", exc_info=True,
                     extra={"request_id": getattr(request.state, "request_id", None)})
        raise HTTPException(status_code=500, detail=f"Vision error: {exc}") from exc

    logger.info("Image described", extra={
        "request_id": getattr(request.state, "request_id", None),
        "input_type": "image",
        "file_name": file.filename,
        "model_used": model_used,
    })

    return IngestImageResponse(
        description=description,
        filename=file.filename or "upload",
        model_used=model_used,
    )
