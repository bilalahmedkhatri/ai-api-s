"""tests/test_tts_audio.py — Unit tests for Kokoro TTS audio generation endpoints."""

from unittest.mock import patch
import pytest
from httpx import ASGITransport, AsyncClient
from main import app
from app.api.v1.audio.schemas import TTSRequest


@pytest.mark.asyncio
async def test_tts_request_schema_validation():
    req = TTSRequest(text="Hello world", voice="af_sarah", speed=1.2)
    assert req.text == "Hello world"
    assert req.voice == "af_sarah"
    assert req.speed == 1.2


@pytest.mark.asyncio
async def test_tts_speech_endpoint_missing_model_files(monkeypatch):
    """When ONNX model files are missing, returns 503 Service Unavailable."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        resp = await ac.post(
            "/api/v1/audio/speech",
            json={"text": "Testing Kokoro TTS model", "voice": "af_sarah"},
        )
        # Without kokoro-v1_0.onnx downloaded locally, service returns 503
        assert resp.status_code in (503, 500)


@pytest.mark.asyncio
async def test_tts_speech_endpoint_success_mock():
    """Mock Kokoro TTS service call to verify HTTP 200 WAV audio response structure."""
    fake_wav_bytes = b"RIFF....WAVEfmt ....data...."
    fake_sample_rate = 24000

    async def mock_generate_speech(text, voice=None, speed=1.0, lang="en-us"):
        return fake_wav_bytes, fake_sample_rate

    with patch("app.api.v1.audio.router.generate_speech", side_effect=mock_generate_speech):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            resp = await ac.post(
                "/api/v1/audio/speech",
                json={"text": "Synthesize this text into Kokoro audio speech", "voice": "af_sarah", "speed": 1.0},
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "audio/wav"
            assert resp.headers["x-sample-rate"] == "24000"
            assert resp.content == fake_wav_bytes
