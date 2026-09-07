"""app/api/v1/audio/schemas.py — Flexible Pydantic schemas for audio endpoints."""

from typing import Annotated
from pydantic import AliasChoices, BaseModel, Field, field_validator


class TTSRequest(BaseModel):
    text: str = Field(
        ...,
        validation_alias=AliasChoices("text", "input", "prompt"),
        min_length=1,
        max_length=50000,
        description="Text to synthesize into speech (supports text, input, or prompt fields up to 50,000 chars)",
    )
    voice: str | None = Field(default=None, description="Voice identifier e.g. 'af_sarah', 'am_adam', 'af_bella'")
    speed: float | None = Field(default=1.0, ge=0.1, le=5.0, description="Speech rate multiplier (0.1 to 5.0)")
    lang: str | None = Field(default="en-us", description="Language code e.g. 'en-us', 'en-gb'")

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
