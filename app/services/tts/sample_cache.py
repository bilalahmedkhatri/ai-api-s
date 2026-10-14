"""app/services/tts/sample_cache.py — safe on-disk cache for voice samples."""

import os
import uuid
from pathlib import Path

from app.services.tts.constants import SAFE_NAME_RE, SAMPLES_DIR


def _sample_path(voice: str, model: str) -> Path:
    """Validate names and make sure the final path stays inside SAMPLES_DIR."""
    if not SAFE_NAME_RE.fullmatch(model or "") or not SAFE_NAME_RE.fullmatch(voice or ""):
        raise ValueError("Invalid model/voice name.")
    if ".." in voice or ".." in model:
        raise ValueError("Invalid model/voice name.")
    path = (SAMPLES_DIR / model / f"{voice}.wav").resolve()
    if not path.is_relative_to(SAMPLES_DIR.resolve()):
        raise ValueError("Invalid sample path.")
    return path


def get_cached_voice_sample(voice: str, model: str) -> tuple[bytes, str] | None:
    """Return pre-generated WAV bytes from local disk cache if available."""
    try:
        target = _sample_path(voice, model)
    except ValueError:
        return None
    if target.exists() and target.stat().st_size > 0:
        return target.read_bytes(), "audio/wav"
    return None


def cache_voice_sample(voice: str, model: str, wav_bytes: bytes) -> None:
    """Save generated sample atomically (no half-written files)."""
    target = _sample_path(voice, model)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(wav_bytes)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
