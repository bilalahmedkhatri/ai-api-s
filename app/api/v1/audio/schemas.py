"""app/api/v1/audio/schemas.py — Flexible Pydantic schemas for audio endpoints."""

from typing import Annotated
from pydantic import AliasChoices, BaseModel, Field, field_validator


class DynamicTTSRequest(BaseModel):
    title: str | None = Field(
        default=None,
        max_length=100,
        description="Optional title for the voiceover job",
    )
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
    provider: str | None = Field(
        default=None,
        description="Optional provider identifier (e.g. gemini, kokoro)",
    )
    voice: str | None = Field(default=None, description="Voice identifier. If omitted, model default is used.")
    lang: str | None = Field(default="en-us", description="Language code (for supported models)")
    extra_params: dict | None = Field(default=None, description="Optional extra parameters for specific models")

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("Text for speech generation cannot be empty.")
        return str(v)


# Backward-compatible alias for existing tests
TTSRequest = DynamicTTSRequest


class RenewAudioUrlRequest(BaseModel):
    job_id: str | None = Field(default=None, description="The UUID job_id of the TTS job to renew.")
    url: str | None = Field(default=None, description="The expired or existing Backblaze audio URL.")

    @field_validator("job_id", "url", mode="before")
    @classmethod
    def clean_strings(cls, v: str | None) -> str | None:
        if v is not None and isinstance(v, str):
            cleaned = v.strip()
            return cleaned if cleaned else None
        return v
