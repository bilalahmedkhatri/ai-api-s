"""Shared Pydantic v2 response schemas for all /ingest endpoints."""

from pydantic import BaseModel


class IngestAudioResponse(BaseModel):
    input_type: str = "voice"
    transcript: str
    language: str | None = None
    duration_s: float | None = None


class IngestImageResponse(BaseModel):
    input_type: str = "image"
    description: str
    filename: str
    model_used: str | None = None


class IngestVideoResponse(BaseModel):
    input_type: str = "video"
    keyframes_extracted: int
    summary: str
    filename: str
