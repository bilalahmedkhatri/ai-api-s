"""app/services/tts_service.py — Dynamic Database-Driven TTS Service."""

import asyncio
import io
import logging
import re
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


def merge_wav_bytes(wav_list: list[bytes]) -> bytes:
    """Merge multiple WAV byte strings into a single WAV file."""
    if not wav_list:
        return b""
    if len(wav_list) == 1:
        return wav_list[0]

    import io
    import wave

    data = []
    params = None

    for wav in wav_list:
        with wave.open(io.BytesIO(wav), 'rb') as w:
            if params is None:
                params = w.getparams()
            data.append(w.readframes(w.getnframes()))

    out_io = io.BytesIO()
    with wave.open(out_io, 'wb') as w_out:
        w_out.setparams(params)
        for d in data:
            w_out.writeframes(d)

    return out_io.getvalue()


_GEMINI_TTS_TIMEOUT = 150.0   # seconds — Gemini TTS for long texts can take 40-60 s
_GEMINI_TTS_MAX_RETRIES = 10  # retry on transient network / timeout errors


async def _generate_gemini_speech(
    text: str,
    voice: str,
    url: str,
    config: dict,
    api_key: str | None,
    db: AsyncSession | None = None,
    provider: str = "gemini",
) -> tuple[bytes, str]:
    """Generate audio via Gemini API with retry on transient errors and DB-backed key rotation on 429."""
    import base64
    import copy

    import httpx

    from app.db.database import AsyncSessionLocal
    from app.services.key_manager import (
        get_next_available_key,
        mark_key_quota_exceeded,
        mark_key_used,
    )

    # --- Key resolution ---
    # Priority: caller-supplied key > DB pool > settings fallback
    active_key_id: int | None = None
    active_key: str

    if api_key:
        active_key = api_key
        use_db_rotation = False
    else:
        # Try DB pool first
        _db = db
        _own_db = False
        if _db is None:
            _db = AsyncSessionLocal()
            _own_db = True
        try:
            result = await get_next_available_key(_db, provider=provider)
        finally:
            if _own_db:
                await _db.aclose()

        if result:
            active_key_id, active_key = result
            use_db_rotation = True
        else:
            # Fall back to settings
            fallback = settings.gemini_api_key
            if not fallback:
                raise ValueError("No Gemini API keys configured. Add keys via POST /api/v1/keys/provider or set GEMINI_API_KEY in .env")
            active_key = fallback
            use_db_rotation = False

    chunks = split_text_into_chunks(text, max_chars=1500)
    all_wavs = []
    mime_type = "audio/wav"

    async with httpx.AsyncClient(timeout=_GEMINI_TTS_TIMEOUT) as client:
        for i, chunk in enumerate(chunks):
            payload = {
                "contents": [{"parts": [{"text": chunk.strip()}]}],
                "generationConfig": copy.deepcopy(config) if config else {
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
            chunk_success = False

            for attempt in range(_GEMINI_TTS_MAX_RETRIES + 1):
                if attempt:
                    wait = 2 ** attempt  # 2 s, 4 s
                    logger.warning(
                        "Gemini TTS attempt %d/%d failed for a chunk (%s), retrying in %s s…",
                        attempt, _GEMINI_TTS_MAX_RETRIES, last_exc, wait,
                    )
                    await asyncio.sleep(wait)

                try:
                    resp = await client.post(f"{url}?key={active_key}", json=payload)

                    if resp.status_code == 200:
                        data = resp.json()
                        try:
                            part = data["candidates"][0]["content"]["parts"][0]
                            inline_data = part.get("inlineData") or part.get("inline_data") or {}
                            chunk_mime = inline_data.get("mimeType") or inline_data.get("mime_type", "audio/wav")
                            mime_type = chunk_mime
                            wav_bytes = pcm_to_wav(base64.b64decode(inline_data["data"]))
                            all_wavs.append(wav_bytes)
                            chunk_success = True
                            # Track usage in DB
                            if use_db_rotation and active_key_id is not None:
                                try:
                                    async with AsyncSessionLocal() as _track_db:
                                        await mark_key_used(_track_db, active_key_id)
                                except Exception:
                                    pass  # non-critical
                            break
                        except Exception as exc:
                            raise RuntimeError(f"Unexpected Gemini API response format: {data}") from exc

                    # --- 429 Quota Exceeded: DB-backed rotation ---
                    if resp.status_code == 429:
                        if use_db_rotation:
                            # Mark current key as exhausted in DB
                            if active_key_id is not None:
                                try:
                                    async with AsyncSessionLocal() as _quota_db:
                                        await mark_key_quota_exceeded(_quota_db, active_key_id)
                                except Exception:
                                    pass
                            # Fetch next available key from DB
                            async with AsyncSessionLocal() as _rot_db:
                                next_result = await get_next_available_key(_rot_db, provider=provider)
                            if next_result:
                                active_key_id, active_key = next_result
                                logger.warning("Gemini 429 — rotated to next DB key (id=%d)", active_key_id)
                                last_exc = RuntimeError("Key quota exceeded, rotated to next DB key")
                                continue  # retry same chunk with new key immediately
                        # No more keys left (or caller supplied a fixed key)
                        raise RuntimeError(f"GEMINI_QUOTA_EXCEEDED: {resp.text}")

                    # If Gemini TTS throws a 400 because of strict conversational safety filter
                    # We gracefully fallback by removing speechConfig
                    if resp.status_code == 400 and "speechConfig" in payload.get("generationConfig", {}):
                        logger.warning("Gemini TTS 400 error (strict filter), retrying without speechConfig: %s", resp.text)
                        del payload["generationConfig"]["speechConfig"]
                        continue

                    # Non-retryable HTTP error (e.g. 400 bad request, 403 auth)
                    raise RuntimeError(f"Gemini TTS API error {resp.status_code}: {resp.text}")

                except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
                    last_exc = exc
                    continue  # retry

            if not chunk_success:
                raise RuntimeError(
                    f"Gemini TTS failed after {_GEMINI_TTS_MAX_RETRIES + 1} attempts for a chunk: {last_exc}"
                ) from last_exc

            # Delay before generating the next chunk (unless it's the last one)
            if i < len(chunks) - 1:
                await asyncio.sleep(1.5)

    if not all_wavs:
        raise RuntimeError("Failed to generate audio for any text chunks.")

    merged_wav = merge_wav_bytes(all_wavs)
    return merged_wav, mime_type



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
    lang: str | None = None,
    extra_params: dict | None = None,
    api_key: str | None = None,
    is_sample_request: bool = False,
    provider: str | None = None,
    speed: float | None = None,
) -> tuple[bytes, str]:
    """
    Main Dynamic Factory to route TTS generation based on database configuration.
    Handles Gemini API, Kokoro Local, and other providers dynamically.
    """
    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")

    stmt = select(TTSModel).where(TTSModel.name == model_name, TTSModel.is_active == True)
    if provider:
        stmt = stmt.where(TTSModel.provider.ilike(provider.strip()))
    model = (await db.execute(stmt)).scalar_one_or_none()
    if not model:
        raise ValueError(f"Model '{model_name}' not found")

    target_voice = voice or model.default_voice

    # 1. Check Cache for samples
    if is_sample_request:
        cached = get_cached_voice_sample(target_voice, model_name)
        if cached:
            return cached

    # 2. Route to provider dynamically
    provider_name = model.provider.lower()

    if provider_name == "gemini":
        wav_bytes, mime = await _generate_gemini_speech(
            text=text,
            voice=target_voice,
            url=model.api_endpoint_url,
            config=model.provider_config,
            api_key=api_key,
            db=db,
            provider=provider_name,
        )
    elif provider_name == "kokoro":
        if model.is_local:
            target_speed = speed or (extra_params.get("speed") if extra_params else None) or model.default_speed
            target_lang = lang or (extra_params.get("lang") if extra_params else None) or model.default_lang
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
        raise ValueError(f"Unsupported provider: {provider_name}")

    # 3. Cache if it was a sample request
    if is_sample_request:
        try:
            cache_voice_sample(target_voice, model_name, wav_bytes)
        except Exception as e:
            logger.warning("Failed to cache sample: %s", e)

    return wav_bytes, mime


async def process_tts_job(
    job_id: str,
    model_name: str,
    text: str,
    voice: str | None,
    lang: str | None,
    extra_params: dict | None = None,
    provider: str | None = None,
):
    """Background task to generate TTS, upload to B2, and update job status."""
    import uuid

    from sqlalchemy import update

    from app.core.config import settings
    from app.db.database import AsyncSessionLocal
    from app.models.db_models import TTSJob
    from app.services.media_extractor import get_b2_session

    async with AsyncSessionLocal() as db:
        try:
            # Generate the audio (extra_params forwarded directly)
            wav_bytes, _ = await generate_dynamic_speech(
                db=db,
                model_name=model_name,
                text=text,
                voice=voice,
                lang=lang,
                extra_params=extra_params,
                is_sample_request=False,
                provider=provider,
            )

            # Upload to B2
            filename = f"tts_{job_id}_{uuid.uuid4().hex[:8]}.wav"
            object_key = f"tts_audio/{filename}"

            session, b2_config = get_b2_session()
            async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=b2_config) as s3:
                await s3.put_object(
                    Bucket=settings.b2_bucket_name,
                    Key=object_key,
                    Body=wav_bytes,
                    ContentType="audio/wav"
                )

            # Generate 7-day S3 pre-signed URL via dedicated voice_url_service
            import datetime

            from app.services.voice_url_service import generate_audio_presigned_url
            audio_url = await generate_audio_presigned_url(object_key)
            now_utc = datetime.datetime.now(datetime.UTC)

            # Update Database
            await db.execute(
                update(TTSJob)
                .where(TTSJob.job_id == job_id)
                .values(status="completed", audio_url=audio_url, updated_at=now_utc)
            )
            await db.commit()
            logger.info("TTS Job %s completed successfully with 7-day presigned URL.", job_id)

        except Exception as e:
            logger.error("TTS Job %s failed: %s", job_id, e, exc_info=True)
            import datetime
            await db.execute(
                update(TTSJob)
                .where(TTSJob.job_id == job_id)
                .values(status="failed", updated_at=datetime.datetime.now(datetime.UTC))
            )
            await db.commit()
