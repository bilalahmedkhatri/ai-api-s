"""app/services/tts/gemini_provider.py — Gemini TTS with retry + DB key rotation."""

import asyncio
import base64
import copy
import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.services.generation_telemetry import begin_attempt, http_usage, provider_attempt
from app.services.key_manager import (
    get_next_available_key,
    mark_key_quota_exceeded,
    mark_key_used,
)
from app.services.tts.audio_utils import merge_wav_bytes, pcm_to_wav
from app.services.tts.constants import (
    DEFAULT_SAMPLE_RATE,
    GEMINI_BACKOFF_CAP,
    GEMINI_CHUNK_MAX_CHARS,
    GEMINI_INTER_CHUNK_DELAY,
    GEMINI_RETRYABLE_STATUS,
    GEMINI_TTS_MAX_RETRIES,
    GEMINI_TTS_TIMEOUT,
)
from app.services.tts.text_utils import split_text_into_chunks

logger = logging.getLogger(__name__)
_request_lock = asyncio.Lock()
_next_request_at = 0.0
_key_lock = asyncio.Lock()
_key_cursor: dict[str, int] = {}
_key_requests: dict[str, asyncio.Semaphore] = {}
_fallback_cursor = 0


async def _next_pool_key(provider):
    async with _key_lock:
        async with AsyncSessionLocal() as session:
            result = await get_next_available_key(session, provider=provider, after_id=_key_cursor.get(provider))
        if result:
            _key_cursor[provider] = result[0]
            logger.info('TTS provider key selected provider=%s key_id=%s', provider, result[0])
        return result


async def _pace_request():
    """Space request starts (including retries) across jobs in this process."""
    global _next_request_at
    async with _request_lock:
        loop = asyncio.get_running_loop()
        delay = max(0, _next_request_at - loop.time())
        if delay:
            await asyncio.sleep(delay)
        _next_request_at = loop.time() + settings.tts_gemini_request_interval

# kept for backward compatibility with old imports
_GEMINI_TTS_TIMEOUT = GEMINI_TTS_TIMEOUT
_GEMINI_TTS_MAX_RETRIES = GEMINI_TTS_MAX_RETRIES


@dataclass
class _ActiveKey:
    value: str
    key_id: int | None = None
    rotating: bool = False   # True when the key comes from the DB pool


def _backoff(attempt: int) -> float:
    """2, 4, 8, 16 … capped, plus jitter."""
    return min(2 ** attempt, GEMINI_BACKOFF_CAP) + random.uniform(0, 1)


async def _resolve_key(api_key: str | None, provider: str) -> _ActiveKey:
    """Priority: caller key > DB pool > settings fallback. Always uses a short-lived session."""
    if api_key:
        return _ActiveKey(api_key)

    result = await _next_pool_key(provider)
    if result:
        key_id, key = result
        return _ActiveKey(key, key_id, rotating=True)

    pool = list(dict.fromkeys(settings.gemini_api_keys_pool))
    if not pool:
        raise ValueError(
            "No Gemini API keys configured. Add keys via POST /api/v1/keys/provider "
            "or set GEMINI_API_KEY in .env"
        )
    global _fallback_cursor
    key = pool[_fallback_cursor % len(pool)]
    _fallback_cursor += 1
    return _ActiveKey(key)


def _build_payload(chunk: str, voice: str, config: dict) -> dict:
    gen_cfg = copy.deepcopy(config) if config else {}
    gen_cfg.setdefault("responseModalities", ["AUDIO"])
    # requested voice always overrides whatever is stored in DB config
    voice_cfg = (
        gen_cfg.setdefault("speechConfig", {})
        .setdefault("voiceConfig", {})
        .setdefault("prebuiltVoiceConfig", {})
    )
    voice_cfg["voiceName"] = voice
    return {"contents": [{"parts": [{"text": chunk.strip()}]}], "generationConfig": gen_cfg}


def _extract_wav(data: dict) -> bytes:
    """Pull PCM out of the Gemini response and wrap it as WAV (uses the rate in the mime)."""
    part = data["candidates"][0]["content"]["parts"][0]
    inline = part.get("inlineData") or part.get("inline_data") or {}
    mime = inline.get("mimeType") or inline.get("mime_type") or ""
    m = re.search(r"rate=(\d+)", mime)
    rate = int(m.group(1)) if m else DEFAULT_SAMPLE_RATE
    return pcm_to_wav(base64.b64decode(inline["data"]), sample_rate=rate)


async def _mark_used(key: _ActiveKey) -> None:
    if key.rotating and key.key_id is not None:
        try:
            async with AsyncSessionLocal() as s:
                await mark_key_used(s, key.key_id)
        except Exception:  # non-critical
            pass


async def _synthesize_chunk(
    client: httpx.AsyncClient, url: str, payload: dict, key: _ActiveKey, provider: str
) -> bytes:
    last_exc: Exception | None = None
    skip_wait = False

    for attempt in range(GEMINI_TTS_MAX_RETRIES + 1):
        if attempt and not skip_wait:
            wait = _backoff(attempt)
            logger.warning(
                "Gemini TTS attempt %d/%d failed (%s), retrying in %.1fs",
                attempt, GEMINI_TTS_MAX_RETRIES, type(last_exc).__name__, wait,
            )
            await asyncio.sleep(wait)
        skip_wait = False

        try:
            identity = hashlib.sha256(key.value.encode()).hexdigest()
            async with _key_requests.setdefault(identity, asyncio.Semaphore(settings.tts_gemini_key_concurrency)):
                await _pace_request()
                # Bound simultaneous calls without serializing an entire job on one key.
                started = time.monotonic()
                async with provider_attempt("tts") as measurement:
                    await begin_attempt()
                    resp = await client.post(url, json=payload, headers={"x-goog-api-key": key.value})
                    http_usage(measurement, resp, gemini=True)
                    resp.raise_for_status()
                    # A 200 response without audio is a failed generation attempt.
                    wav = _extract_wav(resp.json())
                logger.info('TTS provider response provider=%s key_id=%s status=%d elapsed_sec=%.2f',
                            provider, key.key_id, resp.status_code, time.monotonic() - started)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
            last_exc = exc
            continue
        except httpx.HTTPStatusError as exc:
            resp = exc.response
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_exc = RuntimeError(f"Gemini returned no audio ({type(exc).__name__})")
            continue

        if resp.status_code == 200:
            await _mark_used(key)
            return wav

        if resp.status_code == 429:
            if key.rotating:
                if key.key_id is not None:
                    try:
                        async with AsyncSessionLocal() as s:
                            await mark_key_quota_exceeded(s, key.key_id)
                    except Exception:
                        pass
                nxt = await _next_pool_key(provider)
                if nxt:
                    key.key_id, key.value = nxt
                    logger.warning("Gemini 429 — rotated to next DB key (id=%s)", key.key_id)
                    last_exc = RuntimeError("GEMINI_QUOTA_EXCEEDED: rotated to next DB key")
                    skip_wait = True  # retry immediately with the fresh key
                    continue
            logger.warning("Gemini quota exhausted HTTP=429 key_id=%s provider=%s", key.key_id, provider)
            raise RuntimeError("GEMINI_QUOTA_EXCEEDED: provider returned HTTP 429")

        if resp.status_code in GEMINI_RETRYABLE_STATUS:
            last_exc = RuntimeError(f"Gemini HTTP {resp.status_code}")
            continue

        raise RuntimeError(f"Gemini TTS API error HTTP={resp.status_code}")

    raise RuntimeError(
        f"Gemini TTS failed after {GEMINI_TTS_MAX_RETRIES + 1} attempts for a chunk: {last_exc}"
    ) from last_exc


async def _generate_gemini_speech(
    text: str,
    voice: str,
    url: str,
    config: dict,
    api_key: str | None,
    db: AsyncSession | None = None,  # kept for compatibility; short-lived sessions are used instead
    provider: str = "gemini",
) -> tuple[bytes, str]:
    """Generate audio via Gemini API (retry on transient errors, DB key rotation on 429)."""
    key = await _resolve_key(api_key, provider)
    chunks = split_text_into_chunks(text, max_chars=GEMINI_CHUNK_MAX_CHARS)
    wavs: list[bytes] = []

    async with httpx.AsyncClient(timeout=GEMINI_TTS_TIMEOUT) as client:
        for i, chunk in enumerate(chunks):
            payload = _build_payload(chunk, voice, config)
            wavs.append(await _synthesize_chunk(client, url, payload, key, provider))
            if i < len(chunks) - 1:
                await asyncio.sleep(GEMINI_INTER_CHUNK_DELAY)

    if not wavs:
        raise RuntimeError("Failed to generate audio for any text chunks.")

    merged = await asyncio.to_thread(merge_wav_bytes, wavs)
    return merged, "audio/wav"  # bytes are always WAV after pcm_to_wav
