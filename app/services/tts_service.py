"""app/services/tts_service.py — Kokoro Text-to-Speech (TTS) Service using asyncio.to_thread."""

import asyncio
import io
import logging
import os
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global cached Kokoro instance
_kokoro_instance = None
_kokoro_lock = asyncio.Lock()


def get_kokoro_model():
    """Lazy load and cache Kokoro ONNX model instance."""
    global _kokoro_instance
    if _kokoro_instance is not None:
        return _kokoro_instance

    try:
        from kokoro_onnx import Kokoro  # noqa: PLC0415
    except ImportError as exc:
        logger.error("kokoro-onnx library is not installed: %s", exc)
        raise RuntimeError("kokoro-onnx package is not installed on the server.") from exc

    model_path = Path(settings.kokoro_model_path)
    voices_path = Path(settings.kokoro_voices_path)

    # Check if local model files exist
    if not model_path.exists() or not voices_path.exists():
        logger.warning(
            "Kokoro model files not found locally at %s / %s.",
            model_path,
            voices_path,
        )
        raise FileNotFoundError(
            f"Kokoro ONNX model files missing: '{model_path}' or '{voices_path}'. "
            "Please download kokoro-v1_0.onnx and voices-v1_0.bin into project directory."
        )

    logger.info("Initializing Kokoro ONNX model from %s...", model_path)
    _kokoro_instance = Kokoro(str(model_path), str(voices_path))
    return _kokoro_instance


def get_available_voices() -> list[str]:
    """Return list of available voice identifiers in Kokoro voices.bin."""
    try:
        kokoro = get_kokoro_model()
        return kokoro.get_voices()
    except Exception as exc:
        logger.error("Error retrieving Kokoro voices list: %s", exc)
        return [
            "af_sarah", "af_bella", "af_heart", "af_alloy", "af_aoede", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river", "af_sky",
            "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
            "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
            "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
        ]


def _generate_speech_cpu(
    text: str,
    voice: str | None = None,
    speed: float = 1.0,
    lang: str = "en-us",
) -> tuple[bytes, int]:
    """
    Synchronous CPU-bound audio generation worker.
    Runs inside worker thread pool via asyncio.to_thread().
    """
    import soundfile as sf  # noqa: PLC0415

    kokoro = get_kokoro_model()
    voice_name = voice or settings.kokoro_default_voice

    logger.info("Generating Kokoro TTS audio for text length=%d, voice=%s, speed=%.2f", len(text), voice_name, speed)
    samples, sample_rate = kokoro.create(text, voice=voice_name, speed=speed, lang=lang)

    # Convert audio numpy samples to WAV bytes buffer
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    wav_bytes = buf.getvalue()

    return wav_bytes, sample_rate


async def generate_speech(
    text: str,
    voice: str | None = None,
    speed: float = 1.0,
    lang: str = "en-us",
) -> tuple[bytes, int]:
    """
    Async non-blocking entry point for Kokoro TTS.
    Offloads CPU heavy ONNX inference to asyncio.to_thread().
    """
    if not text or not text.strip():
        raise ValueError("Text for speech generation cannot be empty.")

    # Execute CPU-bound inference safely in thread pool
    return await asyncio.to_thread(
        _generate_speech_cpu,
        text=text.strip(),
        voice=voice,
        speed=speed,
        lang=lang,
    )
