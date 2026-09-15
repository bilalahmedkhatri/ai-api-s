"""app/api/v1/video/schemas.py — Schemas for video generation endpoints."""

from pydantic import BaseModel, Field


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    model: str = Field(default="luma-dream-machine")
    negative_prompt: str | None = None
    width: int | None = Field(default=None, ge=64, le=2048)
    height: int | None = Field(default=None, ge=64, le=2048)
    duration_s: float | None = Field(default=None, ge=1, le=30)
    fps: int | None = Field(default=None, ge=8, le=60)
    aspect_ratio: str | None = None
    num_outputs: int = Field(default=1, ge=1, le=4)
    extra_params: dict | None = None


class VideoGenerateResponse(BaseModel):
    model: str
    prompt: str
    media_type: str
    outputs: list[str]
    elapsed_ms: float
    width: int | None
    height: int | None
    duration_s: float | None
