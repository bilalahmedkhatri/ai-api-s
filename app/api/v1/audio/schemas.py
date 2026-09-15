"""app/api/v1/audio/schemas.py — Flexible Pydantic schemas for audio endpoints."""

from typing import Annotated
from pydantic import AliasChoices, BaseModel, Field, field_validator


class DynamicTTSRequest(BaseModel):
    text: str = Field(
        ...,
        validation_alias=AliasChoices("text", "input", "prompt"),
        min_length=1,
        max_length=50000,
        description="Text to synthesize into speech",
    )
    model: str = Field(
        default="gemini-2.5-flash-preview-tts",
        description="The target TTS model identifier from the database",
    )
    voice: str | None = Field(default=None, description="Voice identifier. If omitted, model default is used.")
    speed: float | None = Field(default=1.0, ge=0.1, le=5.0, description="Speech rate multiplier (for supported models)")
    lang: str | None = Field(default="en-us", description="Language code (for supported models)")

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("Text for speech generation cannot be empty.")
        return str(v)

    @field_validator("speed", mode="before")
    @classmethod
    def validate_speed(cls, v: float | int | str | None) -> float:
        if v is None:
            return 1.0
        try:
            val = float(v)
            return max(0.1, min(5.0, val))
        except (ValueError, TypeError):
            return 1.0
