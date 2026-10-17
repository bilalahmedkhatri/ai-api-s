"""Remote failures must not silently start inference on the gateway."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.services.tts import kokoro_provider


@pytest.mark.parametrize("policy", [None, False, "true", 1])
async def test_missing_remote_credentials_does_not_enable_local(policy, monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    local = AsyncMock()
    monkeypatch.setattr(kokoro_provider, "generate_kokoro_local", local)

    with pytest.raises(RuntimeError, match="local fallback is disabled"):
        await kokoro_provider._generate_kokoro_live(
            "test", "af_sarah", config={"allow_local_fallback": policy}
        )
    local.assert_not_awaited()


async def test_explicit_local_fallback_preserves_local_generation(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    local = AsyncMock(return_value=(b"fake-wav", 24000))
    monkeypatch.setattr(kokoro_provider, "generate_kokoro_local", local)

    result = await kokoro_provider._generate_kokoro_live(
        "test", "af_sarah", config={"allow_local_fallback": True}
    )
    assert result == (b"fake-wav", "audio/wav")
    local.assert_awaited_once()


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_remote_http_failure_never_implicitly_runs_local(status, monkeypatch):
    client = AsyncMock()
    client.post.return_value = SimpleNamespace(status_code=status)
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: context)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "isolated-fake-key")
    local = AsyncMock()
    monkeypatch.setattr(kokoro_provider, "generate_kokoro_local", local)

    with pytest.raises(RuntimeError, match="local fallback is disabled"):
        await kokoro_provider._generate_kokoro_live("test", "af_sarah")
    local.assert_not_awaited()
