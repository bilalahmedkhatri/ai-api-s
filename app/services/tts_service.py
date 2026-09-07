"""app/services/tts_service.py — Kokoro Text-to-Speech (TTS) Service using asyncio.to_thread and smart text chunking."""

import asyncio
import io
import logging
import re
from pathlib import Path

import numpy as np
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


def split_text_into_chunks(text: str, max_chars: int = 400) -> list[str]:
    """
    Split long text into sentence/paragraph chunks for optimal TTS phonemization
    and memory usage, avoiding phonemizer line mismatch warnings.
    """
    cleaned_text = text.strip()
    if not cleaned_text:
        return []

    # Split by newlines first
    paragraphs = [p.strip() for p in cleaned_text.split("\n") if p.strip()]
    chunks = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
        else:
            # Split paragraph into sentences by punctuation (. ! ?)
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            current_chunk = ""
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 <= max_chars:
                    current_chunk = f"{current_chunk} {sentence}".strip()
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = sentence
            if current_chunk:
                chunks.append(current_chunk)

    return chunks if chunks else [cleaned_text]


def _generate_speech_cpu(
    text: str,
    voice: str | None = None,
    speed: float = 1.0,
    lang: str = "en-us",
) -> tuple[bytes, int]:
    """
    Synchronous CPU-bound audio generation worker.
    Runs inside worker thread pool via asyncio.to_thread().
    Uses sentence chunking to handle large text contexts (5,000 to 10,000+ chars).
    """
    import soundfile as sf  # noqa: PLC0415

    kokoro = get_kokoro_model()
    voice_name = voice or settings.kokoro_default_voice

    chunks = split_text_into_chunks(text, max_chars=400)
    logger.info(
        "Generating Kokoro TTS audio for total text length=%d across %d chunk(s), voice=%s, speed=%.2f",
        len(text),
        len(chunks),
        voice_name,
        speed,
    )

    all_samples = []
    sample_rate = 24000

    for idx, chunk in enumerate(chunks, 1):
        try:
            samples, sr = kokoro.create(chunk, voice=voice_name, speed=speed, lang=lang)
            sample_rate = sr
            if len(samples) > 0:
                all_samples.append(samples)
        except Exception as exc:
            logger.warning("Error generating audio for chunk %d/%d ('%s...'): %s", idx, len(chunks), chunk[:30], exc)

    if not all_samples:
        raise RuntimeError("Failed to generate audio for any text chunks.")

    # Concatenate all numpy sample arrays cleanly
    final_samples = np.concatenate(all_samples)

    # Convert audio numpy samples to WAV bytes buffer
    buf = io.BytesIO()
    sf.write(buf, final_samples, sample_rate, format="WAV")
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
