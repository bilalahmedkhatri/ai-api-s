"""app/api/v1/audio/schemas.py — Pydantic schemas for audio endpoints."""

from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="Text to synthesize into speech")
    voice: str | None = Field(default=None, description="Voice identifier e.g. 'af_sarah', 'am_adam', 'af_bella'")
    speed: float = Field(default=1.0, ge=0.25, le=4.0, description="Speech rate multiplier (0.25 to 4.0)")
    lang: str = Field(default="en-us", description="Language code e.g. 'en-us', 'en-gb'")
