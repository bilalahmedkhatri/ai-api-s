"""Compatibility import path; the canonical public facade is app.services.tts_service."""

# ruff: noqa: F401
from app.services.tts_service import (
    _GEMINI_TTS_MAX_RETRIES,
    _GEMINI_TTS_TIMEOUT,
    SAMPLES_DIR,
    VOICE_LANG_PREFIX_MAP,
    __all__,
    _generate_gemini_speech,
    _generate_kokoro_live,
    _generate_kokoro_local_cpu,
    _get_kokoro_model,
    _kokoro_instances,
    _resolve_kokoro_lang,
    cache_voice_sample,
    generate_dynamic_speech,
    get_cached_voice_sample,
    get_dynamic_tts_models,
    get_dynamic_voices,
    get_kokoro_model,
    get_wav_stats,
    merge_wav_bytes,
    notify_frontend,
    pcm_to_wav,
    process_tts_job,
    split_text_into_chunks,
)
