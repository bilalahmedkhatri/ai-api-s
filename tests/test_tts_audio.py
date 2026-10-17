"""tests/test_tts_audio.py — Unit tests for Kokoro TTS audio generation endpoints."""

from unittest.mock import patch

import pytest
from app.api.v1.audio.schemas import TTSRequest
from httpx import ASGITransport, AsyncClient
from main import app


@pytest.mark.asyncio
async def test_tts_request_schema_validation():
    req = TTSRequest(text="Hello world", voice="af_sarah", speed=1.2)
    assert req.text == "Hello world"
    assert req.voice == "af_sarah"
    assert req.speed == 1.2


@pytest.mark.asyncio
async def test_tts_sample_endpoint_success_mock():
    """Mock generate_dynamic_speech to verify HTTP 200 WAV audio response structure on /sample."""
    fake_wav_bytes = b"RIFF....WAVEfmt ....data...."

    async def mock_gen(*args, **kwargs):
        return fake_wav_bytes, "audio/wav"

    with patch("app.api.v1.audio.router.generate_dynamic_speech", side_effect=mock_gen):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            resp = await ac.get("/api/v1/audio/sample?model=kokoro-v1.0-live&voice=af_sarah")
            assert resp.status_code == 200
            assert "audio/wav" in resp.headers["content-type"]
            assert resp.content == fake_wav_bytes


@pytest.mark.asyncio
async def test_local_kokoro_no_longer_runs_in_gateway():
    from app.services.tts.kokoro_provider import generate_kokoro_local

    with pytest.raises(RuntimeError, match="worker is not configured"):
        await generate_kokoro_local(
            text="test",
            voice="af_sarah",
            speed=1.0,
            lang="en-us",
            model_name="kokoro-v1.0-local",
            config={},
        )
