"""app/services/tts/kokoro_provider.py — Kokoro (local ONNX + Replicate live)."""

import asyncio
import io
import logging
import os
import threading
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.services.tts.constants import (
    DEFAULT_SAMPLE_RATE,
    KOKORO_CHUNK_GAP_SEC,
    KOKORO_CHUNK_MAX_CHARS,
)
from app.services.tts.text_utils import split_text_into_chunks

logger = logging.getLogger(__name__)

# model_name -> Kokoro instance. threading.Lock because loading happens in worker threads.
_kokoro_instances: dict = {}
_kokoro_lock = threading.Lock()
# Limit parallel CPU inference so concurrent requests don't oversubscribe the CPU.
_kokoro_sem = asyncio.Semaphore(int(getattr(settings, "kokoro_max_concurrency", 2)))

VOICE_LANG_PREFIX_MAP = {
    "a": "en-us",  # American English
    "b": "en-gb",  # British English
    "e": "es",     # Spanish
    "f": "fr-fr",  # French
    "h": "hi",     # Hindi
    "i": "it",     # Italian
    "j": "ja",     # Japanese
    "p": "pt-br",  # Portuguese
    "z": "cmn",    # Mandarin
}


def _resolve_kokoro_lang(voice: str, lang: str | None = None, default_lang: str | None = None) -> str:
    """Infer the correct language code for Kokoro based on voice name prefix if not specified."""
    if lang and lang.strip():
        return lang.strip()
    if default_lang and default_lang.strip():
        return default_lang.strip()
    if voice:
        first_char = voice[0].lower()
        if first_char in VOICE_LANG_PREFIX_MAP:
            return VOICE_LANG_PREFIX_MAP[first_char]
    return "en-us"


def _get_kokoro_model(model_name: str, config: dict | None = None):
    """Lazy load and cache Kokoro ONNX model instances (thread-safe, loaded once)."""
    with _kokoro_lock:
        if model_name in _kokoro_instances:
            return _kokoro_instances[model_name]

        try:
            from kokoro_onnx import Kokoro  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("kokoro-onnx package is not installed on the server.") from exc

        config = config or {}
        model_path = Path(config.get("model_path") or settings.kokoro_model_path)
        voices_path = Path(config.get("voices_path") or settings.kokoro_voices_path)

        if not model_path.exists() or not voices_path.exists():
            raise FileNotFoundError(
                f"Kokoro ONNX model files missing: '{model_path}' or '{voices_path}'."
            )

        _kokoro_instances[model_name] = Kokoro(str(model_path), str(voices_path))
        return _kokoro_instances[model_name]


# Alias for backward compatibility
get_kokoro_model = _get_kokoro_model


def _generate_kokoro_local_cpu(
    text: str, voice: str, speed: float, lang: str, model_name: str, config: dict
) -> tuple[bytes, int]:
    """Synchronous CPU-bound audio generation for Kokoro Local."""
    import soundfile as sf

    kokoro = _get_kokoro_model(model_name, config)
    resolved_lang = _resolve_kokoro_lang(voice, lang)
    chunks = split_text_into_chunks(text, max_chars=KOKORO_CHUNK_MAX_CHARS)

    parts: list[np.ndarray] = []
    sample_rate = DEFAULT_SAMPLE_RATE

    for chunk in chunks:
        samples, sr = kokoro.create(chunk, voice=voice, speed=speed, lang=resolved_lang)
        sample_rate = sr
        if len(samples) > 0:
            if parts:  # short pause between chunks
                parts.append(np.zeros(int(sr * KOKORO_CHUNK_GAP_SEC), dtype=samples.dtype))
            parts.append(samples)

    if not parts:
        raise RuntimeError("Failed to generate audio for any text chunks.")

    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts), sample_rate, format="WAV")
    return buf.getvalue(), sample_rate


async def generate_kokoro_local(
    text: str, voice: str, speed: float, lang: str, model_name: str, config: dict
) -> tuple[bytes, int]:
    """Async wrapper: CPU work in a thread, bounded by a semaphore."""
    async with _kokoro_sem:
        return await asyncio.to_thread(
            _generate_kokoro_local_cpu,
            text=text, voice=voice, speed=speed, lang=lang,
            model_name=model_name, config=config,
        )


async def _generate_kokoro_live(
    text: str,
    voice: str,
    speed: float = 1.0,
    lang: str = "en-us",
    model_name: str = "kokoro-v1.0-live",
    config: dict | None = None,
    url: str | None = None,
) -> tuple[bytes, str]:
    """Kokoro via Replicate (if token configured), automatic fallback to local ONNX."""
    import httpx

    config = config or {}
    replicate_token = (
        os.environ.get("REPLICATE_API_TOKEN")
        or os.environ.get("REPLICATE_API_KEY")
        or config.get("api_key")
    )

    if replicate_token:
        try:
            headers = {
                "Authorization": f"Bearer {replicate_token}",
                "Content-Type": "application/json",
                "Prefer": "wait",
            }
            endpoint = url or "https://api.replicate.com/v1/models/hexgrad/kokoro-tts/predictions"
            payload = {"input": {"text": text, "voice": voice, "speed": speed}}

            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(endpoint, json=payload, headers=headers)
                if res.status_code in (200, 201):
                    data = res.json()
                    output = data.get("output")

                    if data.get("status") in ("starting", "processing"):
                        poll_url = data.get("urls", {}).get("get")
                        for _ in range(30 if poll_url else 0):
                            await asyncio.sleep(1.0)
                            poll_data = (await client.get(poll_url, headers=headers)).json()
                            if poll_data.get("status") == "succeeded":
                                output = poll_data.get("output")
                                break
                            if poll_data.get("status") == "failed":
                                raise RuntimeError(
                                    f"Replicate Kokoro generation failed: {poll_data.get('error')}"
                                )

                    audio_url = (
                        output if isinstance(output, str)
                        else (output[0] if isinstance(output, list) and output else None)
                    )
                    if audio_url:
                        audio_res = await client.get(audio_url)
                        if audio_res.status_code == 200:
                            return audio_res.content, "audio/wav"
                else:
                    logger.warning("Replicate returned HTTP %s, falling back to local.", res.status_code)
        except Exception as e:
            logger.warning(
                "Replicate Kokoro live generation failed (%s), falling back to local Kokoro ONNX: %s",
                type(e).__name__, e,
            )

    wav_bytes, _ = await generate_kokoro_local(
        text=text, voice=voice, speed=speed, lang=lang, model_name=model_name, config=config
    )
    return wav_bytes, "audio/wav"
