"""app/services/tts_service.py — single entry point for all TTS functionality.

The implementation lives in app/services/tts/*. Samples and background jobs
both use these same functions. This module contains no provider implementation.
"""

# ruff: noqa: F401

from app.services.tts.audio_utils import get_wav_stats, merge_wav_bytes, pcm_to_wav
from app.services.tts.catalog import get_dynamic_tts_models, get_dynamic_voices
from app.services.tts.constants import SAMPLES_DIR
from app.services.tts.gemini_provider import (
    _GEMINI_TTS_MAX_RETRIES,
    _GEMINI_TTS_TIMEOUT,
    _generate_gemini_speech,
)
from app.services.tts.generator import generate_dynamic_speech
from app.services.tts.jobs import process_tts_job
from app.services.tts.kokoro_provider import (
    VOICE_LANG_PREFIX_MAP,
    _generate_kokoro_live,
    _generate_kokoro_local_cpu,
    _get_kokoro_model,
    _kokoro_instances,
    _resolve_kokoro_lang,
    get_kokoro_model,
)
from app.services.tts.notifier import notify_frontend
from app.services.tts.sample_cache import cache_voice_sample, get_cached_voice_sample
from app.services.tts.text_utils import split_text_into_chunks

__all__ = [
    "SAMPLES_DIR", "VOICE_LANG_PREFIX_MAP",
    "cache_voice_sample", "generate_dynamic_speech", "get_cached_voice_sample",
    "get_dynamic_tts_models", "get_dynamic_voices", "get_kokoro_model", "get_wav_stats",
    "merge_wav_bytes", "notify_frontend", "pcm_to_wav", "process_tts_job",
    "split_text_into_chunks",
]
