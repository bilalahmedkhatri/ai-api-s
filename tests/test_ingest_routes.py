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
    """All three ingest paths must appear in the OpenAPI schema."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/ingest/audio/" in paths
    assert "/api/v1/ingest/image/" in paths
    assert "/api/v1/ingest/video/" in paths
    assert "/api/v1/audio/gemini-voices" in paths
    assert "/api/v1/audio/gemini-tts" in paths
    assert "/api/v1/audio/gemini-sample" in paths


def test_gemini_voices_endpoint():
    """Verify gemini voices endpoint returns 200 and list of voices with metadata."""
    response = client.get("/api/v1/audio/gemini-voices")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "voices" in data
    voice_names = [v["name"] for v in data["voices"]]
    assert "Puck" in voice_names
    assert "sample_audio_url" in data["voices"][0]





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
