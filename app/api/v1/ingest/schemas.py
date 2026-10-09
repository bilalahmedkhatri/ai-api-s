"""Shared Pydantic v2 response schemas for all /ingest endpoints."""

from pydantic import BaseModel, Field


class IngestAudioUrlRequest(BaseModel):
    url: str = Field(
        ...,
        description="Direct media file URL (.mp3, .mp4, etc.) or streaming platform link (YouTube, Vimeo, etc.)",
        examples=["https://www.youtube.com/watch?v=sample"],
    )
    language: str | None = Field(
        default=None,
        description="Optional 2-character ISO language code (e.g. 'ur', 'en', 'hi', 'fa', 'pa', 'ru'). If omitted, Whisper auto-detects.",
        examples=["ur"],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://www.youtube.com/watch?v=sample",
                "language": "ur",
            }
        }
    }


class AudioSegment(BaseModel):
    id: int
    start: float = Field(..., description="Start timestamp in seconds")
    end: float = Field(..., description="End timestamp in seconds")
    duration: float = Field(default=0.0, description="Segment duration in seconds (end - start)")
    text: str = Field(..., description="Transcribed text for this segment")

    def model_post_init(self, __context) -> None:
        if (not self.duration or self.duration == 0.0) and self.end >= self.start:
            self.duration = round(self.end - self.start, 2)


class IngestAudioResponse(BaseModel):
    input_type: str = "voice"
    transcript: str
    language: str | None = None
    duration_s: float | None = None
    segments: list[AudioSegment] = Field(
        default_factory=list,
        description="Timestamped speech segments with start, end, and duration",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "input_type": "voice",
                "transcript": "Sample transcribed text.",
                "language": "en",
                "duration_s": 4.0,
                "segments": [
                    {
                        "id": 0,
                        "start": 14.0,
                        "end": 18.0,
                        "duration": 4.0,
                        "text": "Sample transcribed text.",
                    }
                ],
            }
        }
    }


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
