"""app/api/v1/image/schemas.py — Schemas for image generation endpoints."""

from pydantic import BaseModel, Field


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    model: str = Field(default="flux-schnell")
    negative_prompt: str | None = None
    width: int | None = Field(default=None, ge=64, le=2048)
    height: int | None = Field(default=None, ge=64, le=2048)
    num_outputs: int = Field(default=1, ge=1, le=4)
    extra_params: dict | None = None


class ImageGenerateResponse(BaseModel):
    model: str
    prompt: str
    media_type: str
    outputs: list[str]
    elapsed_ms: float
    width: int | None
    height: int | None
