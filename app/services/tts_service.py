"""app/services/tts_service.py — Dynamic Database-Driven TTS Service."""

import asyncio
import io
import logging
import re
from pathlib import Path

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.config import settings
from app.models.db_models import TTSModel, TTSVoice

logger = logging.getLogger(__name__)

# Global cached Kokoro instances (model_name -> instance)
_kokoro_instances = {}
_kokoro_lock = asyncio.Lock()


async def get_dynamic_tts_models(db: AsyncSession) -> list[dict]:
    """Fetch all active TTS models from the database."""
    result = await db.execute(select(TTSModel).where(TTSModel.is_active == True))
    models = result.scalars().all()
    return [{"name": m.name, "provider": m.provider, "is_local": m.is_local} for m in models]


from sqlalchemy import func

async def get_dynamic_voices(
    model_name: str, 
    db: AsyncSession,
    search: str | None = None,
    limit: int = 10,
    offset: int = 0
) -> tuple[list[dict], int]:
    """Fetch paginated and searchable voices associated with a specific TTS model."""
    model_result = await db.execute(select(TTSModel).where(TTSModel.name == model_name))
    model = model_result.scalar_one_or_none()
    if not model:
        raise ValueError(f"Model {model_name} not found or inactive.")
        
    stmt = select(TTSVoice).where(TTSVoice.model_id == model.id)
    
    if search:
        # Search by voice_name or gender (case-insensitive)
        stmt = stmt.where(
            TTSVoice.voice_name.ilike(f"%{search}%") | TTSVoice.gender.ilike(f"%{search}%")
        )
        
    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar_one()
    
    # Get paginated data
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    voices = result.scalars().all()
    
    mapped_voices = [
        {
            "name": v.voice_name,
            "gender": v.gender,
            "sample_text": v.sample_text,
            "sample_audio_url": f"/api/v1/audio/sample?model={model_name}&voice={v.voice_name}"
        }
        for v in voices
    ]
    return mapped_voices, total_count


def _get_kokoro_model(model_name: str, config: dict):
    """Lazy load and cache Kokoro ONNX model instances dynamically based on DB config."""
    global _kokoro_instances
    if model_name in _kokoro_instances:
        return _kokoro_instances[model_name]

    try:
        from kokoro_onnx import Kokoro  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("kokoro-onnx package is not installed on the server.") from exc

    model_path = Path(config.get("model_path", "kokoro-v1_0.onnx"))
    voices_path = Path(config.get("voices_path", "voices-v1_0.bin"))

    if not model_path.exists() or not voices_path.exists():
        raise FileNotFoundError(f"Kokoro ONNX model files missing: '{model_path}' or '{voices_path}'.")

    _kokoro_instances[model_name] = Kokoro(str(model_path), str(voices_path))
    return _kokoro_instances[model_name]


def split_text_into_chunks(text: str, max_chars: int = 400) -> list[str]:
    """Split long text into sentence/paragraph chunks for optimal TTS phonemization."""
    cleaned_text = text.strip()
    if not cleaned_text:
        return []
    paragraphs = [p.strip() for p in cleaned_text.split("\n") if p.strip()]
    chunks = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
        else:
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


def _generate_kokoro_local_cpu(text: str, voice: str, speed: float, lang: str, model_name: str, config: dict) -> tuple[bytes, int]:
    """Synchronous CPU-bound audio generation for Kokoro Local."""
    import soundfile as sf
    kokoro = _get_kokoro_model(model_name, config)
    chunks = split_text_into_chunks(text, max_chars=400)
    all_samples = []
    sample_rate = 24000

    for chunk in chunks:
        samples, sr = kokoro.create(chunk, voice=voice, speed=speed, lang=lang)
        sample_rate = sr
        if len(samples) > 0:
            all_samples.append(samples)

    if not all_samples:
        raise RuntimeError("Failed to generate audio for any text chunks.")

    final_samples = np.concatenate(all_samples)
    buf = io.BytesIO()
    sf.write(buf, final_samples, sample_rate, format="WAV")
    return buf.getvalue(), sample_rate


async def _generate_kokoro_live(text: str, voice: str, model_name: str, config: dict, url: str) -> tuple[bytes, str]:
    """Fallback / Live generation for Kokoro via Replicate or similar."""
    raise NotImplementedError("Kokoro live (Replicate) not fully implemented in dynamic handler yet.")


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, num_channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM bytes in a RIFF WAV container if not already WAV formatted."""
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


_GEMINI_TTS_TIMEOUT = 90.0   # seconds — Gemini TTS for long texts can take 40-60 s
_GEMINI_TTS_MAX_RETRIES = 2  # retry on transient network / timeout errors


async def _generate_gemini_speech(text: str, voice: str, url: str, config: dict, api_key: str | None) -> tuple[bytes, str]:
    """Generate audio via Gemini API with retry on transient timeout / network errors."""
    import base64
    import httpx

    key = api_key or settings.gemini_api_key
    if not key:
        raise ValueError("GEMINI_API_KEY environment variable is not configured.")

    payload = {
        "contents": [{"parts": [{"text": text.strip()}]}],
        "generationConfig": config or {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }

    # Ensure the requested voice always overrides whatever is stored in DB config
    gen_cfg = payload["generationConfig"]
    if "speechConfig" not in gen_cfg:
        gen_cfg["speechConfig"] = {"voiceConfig": {"prebuiltVoiceConfig": {}}}
    gen_cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] = voice

    last_exc: Exception | None = None
    for attempt in range(_GEMINI_TTS_MAX_RETRIES + 1):
        if attempt:
            wait = 2 ** attempt  # 2 s, 4 s
            logger.warning(
                "Gemini TTS attempt %d/%d failed (%s), retrying in %s s…",
                attempt, _GEMINI_TTS_MAX_RETRIES, last_exc, wait,
            )
            await asyncio.sleep(wait)

        try:
            async with httpx.AsyncClient(timeout=_GEMINI_TTS_TIMEOUT) as client:
                resp = await client.post(f"{url}?key={key}", json=payload)

            if resp.status_code == 200:
                data = resp.json()
                try:
                    part = data["candidates"][0]["content"]["parts"][0]
                    inline_data = part.get("inlineData") or part.get("inline_data") or {}
                    mime_type = inline_data.get("mimeType") or inline_data.get("mime_type", "audio/wav")
                    wav_bytes = pcm_to_wav(base64.b64decode(inline_data["data"]))
                    return wav_bytes, mime_type
                except Exception as exc:
                    raise RuntimeError(f"Unexpected Gemini API response format: {data}") from exc

            # If Gemini hits a rate limit or quota exceeded
            if resp.status_code == 429:
                raise RuntimeError(f"GEMINI_QUOTA_EXCEEDED: {resp.text}")

            # If Gemini TTS throws a 400 because of strict conversational safety filter (e.g. "Model tried to generate text...")
            # We gracefully fallback by removing speechConfig (which makes it use default voice, but guarantees audio generation)
            if resp.status_code == 400 and "speechConfig" in payload.get("generationConfig", {}):
                logger.warning("Gemini TTS 400 error (strict filter), retrying without speechConfig: %s", resp.text)
                del payload["generationConfig"]["speechConfig"]
                continue

            # Non-retryable HTTP error (e.g. 400 bad request, 403 auth)
            raise RuntimeError(f"Gemini TTS API error {resp.status_code}: {resp.text}")

        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
            last_exc = exc
            continue  # retry

    raise RuntimeError(
        f"Gemini TTS failed after {_GEMINI_TTS_MAX_RETRIES + 1} attempts: {last_exc}"
    ) from last_exc


SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "gemini_samples"

def get_cached_voice_sample(voice: str, model: str) -> tuple[bytes, str] | None:
    """Return pre-generated WAV bytes from local disk cache if available."""
    target_file = SAMPLES_DIR / model / f"{voice}.wav"
    if target_file.exists() and target_file.stat().st_size > 0:
        return target_file.read_bytes(), "audio/wav"
    return None


def cache_voice_sample(voice: str, model: str, wav_bytes: bytes):
    """Save generated sample to disk cache."""
    save_path = SAMPLES_DIR / model / f"{voice}.wav"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(wav_bytes)


async def generate_dynamic_speech(
    db: AsyncSession,
    model_name: str,
    text: str,
    voice: str | None = None,
    speed: float | None = None,
    lang: str | None = None,
    api_key: str | None = None,
    is_sample_request: bool = False
) -> tuple[bytes, str]:
    """
    Main Dynamic Factory to route TTS generation based on SQLite database configuration.
    Handles Gemini API, Kokoro Local, and potentially Replicate/Live integrations automatically.
    """
    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")

    model = await db.execute(select(TTSModel).where(TTSModel.name == model_name, TTSModel.is_active == True))
    model = model.scalar_one_or_none()
    if not model:
        raise ValueError(f"TTS Model '{model_name}' not found or inactive in database.")

    target_voice = voice or model.default_voice
    
    # 1. Check Cache for samples
    if is_sample_request:
        cached = get_cached_voice_sample(target_voice, model_name)
        if cached:
            return cached

    # 2. Route to provider dynamically
    provider = model.provider.lower()
    
    if provider == "gemini":
        wav_bytes, mime = await _generate_gemini_speech(
            text=text, 
            voice=target_voice, 
            url=model.api_endpoint_url, 
            config=model.provider_config, 
            api_key=api_key
        )
    elif provider == "kokoro":
        if model.is_local:
            target_speed = speed or model.default_speed
            target_lang = lang or model.default_lang
            wav_bytes, _ = await asyncio.to_thread(
                _generate_kokoro_local_cpu,
                text=text,
                voice=target_voice,
                speed=target_speed,
                lang=target_lang,
                model_name=model_name,
                config=model.provider_config
            )
            mime = "audio/wav"
        else:
            wav_bytes, mime = await _generate_kokoro_live(
                text=text,
                voice=target_voice,
                model_name=model_name,
                config=model.provider_config,
                url=model.api_endpoint_url
            )
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    # 3. Cache if it was a sample request
    if is_sample_request:
        try:
            cache_voice_sample(target_voice, model_name, wav_bytes)
        except Exception as e:
            logger.warning("Failed to cache sample: %s", e)

    return wav_bytes, mime
