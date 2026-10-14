"""app/services/tts/generator.py — main provider router (generate_dynamic_speech)."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.models.db_models import TTSModel
from app.services.tts.gemini_provider import _generate_gemini_speech
from app.services.tts.kokoro_provider import (
    _generate_kokoro_live,
    _resolve_kokoro_lang,
    generate_kokoro_local,
)
from app.services.tts.sample_cache import cache_voice_sample, get_cached_voice_sample

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ModelInfo:
    """Plain snapshot of a TTSModel row so no DB session/connection is needed afterwards."""
    name: str
    provider: str
    is_local: bool
    api_endpoint_url: str | None
    provider_config: dict
    default_voice: str | None
    default_speed: float | None
    default_lang: str | None


async def _load_model(model_name: str, provider: str | None) -> _ModelInfo | None:
    stmt = select(TTSModel).where(TTSModel.name == model_name, TTSModel.is_active.is_(True))
    if provider:
        stmt = stmt.where(TTSModel.provider.ilike(provider.strip()))

    # Always a short-lived session: closed BEFORE the long TTS call starts.
    async with AsyncSessionLocal() as session:
        m = (await session.execute(stmt)).scalar_one_or_none()
        if not m:
            return None
        return _ModelInfo(
            name=m.name,
            provider=m.provider,
            is_local=bool(m.is_local),
            api_endpoint_url=getattr(m, "api_endpoint_url", None),
            provider_config=dict(getattr(m, "provider_config", None) or {}),
            default_voice=getattr(m, "default_voice", None),
            default_speed=getattr(m, "default_speed", None),
            default_lang=getattr(m, "default_lang", None),
        )


async def generate_dynamic_speech(
    model_name: str,
    text: str,
    db: AsyncSession | None = None,   # kept for API compatibility (not held during generation)
    voice: str | None = None,
    lang: str | None = None,
    extra_params: dict | None = None,
    api_key: str | None = None,
    is_sample_request: bool = False,
    provider: str | None = None,
    speed: float | None = None,
    model_snapshot: dict | None = None,
) -> tuple[bytes, str]:
    """Route TTS generation based on DB config. Returns (wav_bytes, mime_type)."""
    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")

    model = _ModelInfo(**model_snapshot) if model_snapshot else await _load_model(model_name, provider)
    if not model:
        raise ValueError(f"Model '{model_name}' not found")

    target_voice = voice or model.default_voice

    # 1. Sample cache
    if is_sample_request and target_voice:
        cached = get_cached_voice_sample(target_voice, model_name)
        if cached:
            return cached

    # 2. Route to provider
    provider_name = model.provider.lower()

    if provider_name == "gemini":
        wav_bytes, mime = await _generate_gemini_speech(
            text=text,
            voice=target_voice,
            url=model.api_endpoint_url,
            config=model.provider_config,
            api_key=api_key,
            db=None,
            provider=provider_name,
        )
    elif provider_name == "kokoro":
        extra = extra_params or {}
        target_speed = speed or extra.get("speed") or model.default_speed or 1.0
        target_lang = _resolve_kokoro_lang(target_voice, lang or extra.get("lang"), model.default_lang)

        if model.is_local:
            wav_bytes, _ = await generate_kokoro_local(
                text=text, voice=target_voice, speed=target_speed, lang=target_lang,
                model_name=model_name, config=model.provider_config,
            )
            mime = "audio/wav"
        else:
            wav_bytes, mime = await _generate_kokoro_live(
                text=text, voice=target_voice, speed=target_speed, lang=target_lang,
                model_name=model_name, config=model.provider_config, url=model.api_endpoint_url,
            )
    else:
        raise ValueError(f"Unsupported provider: {provider_name}")

    # 3. Cache samples
    if is_sample_request and target_voice:
        try:
            await asyncio.to_thread(cache_voice_sample, target_voice, model_name, wav_bytes)
        except Exception as e:
            logger.warning("Failed to cache sample: %s", e)

    return wav_bytes, mime
