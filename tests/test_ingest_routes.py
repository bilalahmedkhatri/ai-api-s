"""Phase 2 import + route registration tests (no network calls)."""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_routes_registered():
    """All ingest paths must appear in the OpenAPI schema."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/ingest/audio/" in paths
    assert "/api/v1/ingest/image/" in paths
    assert "/api/v1/ingest/video/" in paths
    assert "/api/v1/audio/models" in paths
    assert "/api/v1/audio/voices" in paths
    assert "/api/v1/audio/sample" in paths
    assert "/api/v1/audio/tts" in paths





def test_audio_wrong_format():
    """Non-audio file should return 415."""
    response = client.post(
        "/api/v1/ingest/audio/",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


def test_image_wrong_format():
    """Non-image file should return 415."""
    response = client.post(
        "/api/v1/ingest/image/",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


def test_audio_empty_file():
    """Empty audio upload should return 400."""
    response = client.post(
        "/api/v1/ingest/audio/",
        files={"file": ("test.wav", b"", "audio/wav")},
    )
    assert response.status_code == 400


def test_audio_accepts_video_extension():
    """Video files should pass format validation (return 400 if empty, not 415)."""
    response = client.post(
        "/api/v1/ingest/audio/",
        files={"file": ("sample.mp4", b"", "video/mp4")},
    )
    assert response.status_code == 400


def test_audio_no_file_or_url():
    """Missing both file and url should return 400."""
    response = client.post("/api/v1/ingest/audio/")
    assert response.status_code == 400
    assert "Either a media 'file' upload or a 'url' link must be provided" in response.json()["detail"]


def test_audio_url_exceeds_duration_limit(monkeypatch):
    """When remote media duration exceeds 25 minutes (1500s), returns 400."""
    from app.api.v1.ingest import audio

    monkeypatch.setattr(audio, "_get_media_duration", lambda target, timeout=15: 1800.0)

    response = client.post(
        "/api/v1/ingest/audio/",
        data={"url": "https://example.com/long_video.mp4"},
    )
    assert response.status_code == 400
    assert "exceeds the maximum allowed limit of 25 minutes" in response.json()["detail"]


def test_audio_json_url_exceeds_duration_limit(monkeypatch):
    """When URL is passed as JSON and duration exceeds 25 min, returns 400."""
    from app.api.v1.ingest import audio

    monkeypatch.setattr(audio, "_get_media_duration", lambda target, timeout=15: 1800.0)

    response = client.post(
        "/api/v1/ingest/audio/url",
        json={"url": "https://example.com/long_video.mp4"},
    )
    assert response.status_code == 400
    assert "exceeds the maximum allowed limit of 25 minutes" in response.json()["detail"]


def test_audio_invalid_language_code():
    """Language code not 2 characters should return 400."""
    response = client.post(
        "/api/v1/ingest/audio/",
        data={"url": "https://example.com/test.mp3", "language": "english"},
    )
    assert response.status_code == 400
    assert "Language code must be a 2-character ISO code" in response.json()["detail"]


def test_audio_segments_in_response(monkeypatch):
    """Verify that segments list with start and end times is present in IngestAudioResponse."""
    from app.api.v1.ingest import audio
    from app.api.v1.ingest.schemas import AudioSegment

    monkeypatch.setattr(
        audio,
        "_transcribe_file",
        lambda tmp, language=None, request_id=None: (
            "Hello world",
            "en",
            [AudioSegment(id=0, start=0.0, end=1.5, text="Hello world")],
        ),
    )
    monkeypatch.setattr(audio, "_get_media_duration", lambda target, timeout=15: 1.5)

    response = client.post(
        "/api/v1/ingest/audio/",
        files={"file": ("test.wav", b"fakeaudiobytes", "audio/wav")},
    )
    assert response.status_code == 200
    data = response.json()
    assert "segments" in data
    assert len(data["segments"]) == 1
    assert data["segments"][0]["start"] == 0.0
    assert data["segments"][0]["end"] == 1.5
    assert data["segments"][0]["duration"] == 1.5
    assert data["segments"][0]["text"] == "Hello world"


