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
    assert "/api/v1/ingest/audio/" not in paths
    assert "/api/v1/ingest/audio/url" not in paths
    assert "/api/v1/ingest/image/" in paths
    assert "/api/v1/ingest/video/" in paths
    assert "/api/v1/audio/models" in paths
    assert "/api/v1/audio/voices" in paths
    assert "/api/v1/audio/sample" in paths
    assert "/api/v1/audio/tts" in paths


def test_image_wrong_format():
    """Non-image file should return 415."""
    response = client.post(
        "/api/v1/ingest/image/",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


@pytest.mark.parametrize("path", ["/api/v1/ingest/audio/", "/api/v1/ingest/audio/url"])
def test_transcription_routes_removed(path):
    response = client.post(path, json={"url": "https://example.invalid/audio.wav"})
    assert response.status_code == 404


def test_local_kokoro_requires_worker():
    from app.services.tts.kokoro_provider import _get_kokoro_model

    with pytest.raises(RuntimeError, match="worker is not configured"):
        _get_kokoro_model("kokoro-v1.0-local")
