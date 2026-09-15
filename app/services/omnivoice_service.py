"""
app/services/omnivoice_service.py — Service module for k2-fsa/OmniVoice Hugging Face Space TTS & Voice Cloning.

Provides async non-blocking wrappers using asyncio.to_thread around gradio_client.
"""

import asyncio
import io
import logging
import os
import re
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Global cached client instance
_gradio_client_instance = None
_client_lock = asyncio.Lock()
SPACE_ID = "k2-fsa/OmniVoice"


def normalize_gender(val: str) -> str:
    v = val.strip().lower()
    if "female" in v or "女" in v:
        return "Female / 女"
    if "male" in v or "男" in v:
        return "Male / 男"
    return "Auto"


def normalize_age(val: str) -> str:
    v = val.strip().lower()
    if "child" in v or "儿童" in v:
        return "Child / 儿童"
    if "teen" in v or "少年" in v:
        return "Teenager / 少年"
    if "young" in v or "青年" in v:
        return "Young Adult / 青年"
    if "middle" in v or "中年" in v:
        return "Middle-aged / 中年"
    if "elder" in v or "老年" in v:
        return "Elderly / 老年"
    return "Auto"


def normalize_pitch(val: str) -> str:
    v = val.strip().lower()
    if "very low" in v or "极低" in v:
        return "Very Low Pitch / 极低音调"
    if "low" in v or "低" in v:
        return "Low Pitch / 低音调"
    if "moderate" in v or "medium" in v or "中" in v:
        return "Moderate Pitch / 中音调"
    if "very high" in v or "极高" in v:
        return "Very High Pitch / 极高音调"
    if "high" in v or "高" in v:
        return "High Pitch / 高音调"
    return "Auto"


def normalize_accent(val: str) -> str:
    v = val.strip().lower()
    if "american" in v or "美" in v:
        return "American Accent / 美式口音"
    if "australian" in v or "澳大利亚" in v:
        return "Australian Accent / 澳大利亚口音"
    if "british" in v or "英" in v:
        return "British Accent / 英国口音"
    if "chinese" in v or "中" in v:
        return "Chinese Accent / 中国口音"
    if "canadian" in v or "加拿大" in v:
        return "Canadian Accent / 加拿大口音"
    if "indian" in v or "印度" in v:
        return "Indian Accent / 印度口音"
    if "korean" in v or "韩" in v:
        return "Korean Accent / 韩国口音"
    if "portuguese" in v or "葡萄牙" in v:
        return "Portuguese Accent / 葡萄牙口音"
    if "russian" in v or "俄罗斯" in v:
        return "Russian Accent / 俄罗斯口音"
    if "japanese" in v or "日" in v:
        return "Japanese Accent / 日本口音"
    return "Auto"


def get_omnivoice_client():
    """Lazy initialization of Gradio client for OmniVoice space."""
    global _gradio_client_instance
    if _gradio_client_instance is not None:
        return _gradio_client_instance

    try:
        from gradio_client import Client  # noqa: PLC0415
        hf_token = os.getenv("HF_TOKEN")
        logger.info("Connecting to Hugging Face Space: %s...", SPACE_ID)
        _gradio_client_instance = Client(SPACE_ID, token=hf_token)
        return _gradio_client_instance
    except Exception as exc:
        logger.error("Failed to connect to Hugging Face Space '%s': %s", SPACE_ID, exc)
        raise RuntimeError(f"OmniVoice HF Space unavailable: {exc}") from exc


def split_text_into_chunks(text: str, max_chars: int = 350) -> list[str]:
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


def _voice_design_sync(
    text: str,
    lang: str = "Auto",
    gender: str = "Auto",
    age: str = "Auto",
    pitch: str = "Auto",
    style: str = "Auto",
    accent: str = "Auto",
    dialect: str = "Auto",
    duration: float = 5.0,
    steps: int = 32,
    guidance_scale: float = 2.0,
    denoise: bool = True,
    speed: float = 1.0,
) -> tuple[bytes, str]:
    """Synchronous worker running inside thread pool with sentence/paragraph chunking."""
    client = get_omnivoice_client()
    start_t = time.time()

    gender_str = normalize_gender(gender)
    age_str = normalize_age(age)
    pitch_str = normalize_pitch(pitch)
    accent_str = normalize_accent(accent)

    chunks = split_text_into_chunks(text, max_chars=180)
    logger.info("Executing OmniVoice Voice Design for text length=%d across %d chunk(s), gender=%s, accent=%s", len(text), len(chunks), gender_str, accent_str)

    all_audio_samples = []
    sample_rate = 24000
    last_status = "Done."

    for idx, chunk in enumerate(chunks, 1):
        chunk_dur = min(8.0, max(2.0, len(chunk) / 25.0))
        logger.info("Processing chunk %d/%d (len=%d, dur=%.1fs)...", idx, len(chunks), len(chunk), chunk_dur)

        result = client.predict(
            text=chunk,
            lang=lang,
            ns=float(steps),
            gs=float(guidance_scale),
            dn=denoise,
            sp=float(speed),
            du=chunk_dur,
            pp=True,
            po=True,
            param_9=gender_str,
            param_10=age_str,
            param_11=pitch_str,
            param_12=style,
            param_13=accent_str,
            param_14=dialect,
            api_name="/_design_fn",
        )

        audio_path, status_text = result
        last_status = status_text

        if audio_path and Path(audio_path).exists():
            data, sr = sf.read(audio_path)
            sample_rate = sr
            all_audio_samples.append(data)
        else:
            logger.warning("Failed chunk %d/%d: %s", idx, len(chunks), status_text)

    elapsed = time.time() - start_t
    logger.info("OmniVoice Voice Design completed in %.2fs. Status: %s", elapsed, last_status)

    if not all_audio_samples:
        raise RuntimeError(f"OmniVoice speech synthesis failed: {last_status}")

    combined = np.concatenate(all_audio_samples)
    buf = io.BytesIO()
    sf.write(buf, combined, sample_rate, format="WAV")
    audio_bytes = buf.getvalue()

    return audio_bytes, last_status


def _voice_clone_sync(
    text: str,
    ref_aud: str,
    ref_text: str,
    instruct: str = "Synthesize natural voice",
    lang: str = "Auto",
    duration: float = 5.0,
    steps: int = 32,
    guidance_scale: float = 2.0,
    denoise: bool = True,
    speed: float = 1.0,
) -> tuple[bytes, str]:
    """Synchronous voice cloning worker running inside thread pool."""
    from gradio_client import handle_file  # noqa: PLC0415

    client = get_omnivoice_client()
    start_t = time.time()

    logger.info("Executing OmniVoice Voice Clone for text length=%d with ref_aud=%s", len(text), ref_aud)

    ref_input = handle_file(ref_aud)
    result = client.predict(
        text=text,
        lang=lang,
        ref_aud=ref_input,
        ref_text=ref_text,
        instruct=instruct,
        ns=float(steps),
        gs=float(guidance_scale),
        dn=denoise,
        sp=float(speed),
        du=float(duration),
        pp=True,
        po=True,
        api_name="/_clone_fn",
    )

    elapsed = time.time() - start_t
    audio_path, status_text = result
    logger.info("OmniVoice Voice Clone completed in %.2fs. Status: %s", elapsed, status_text)

    if not audio_path or not Path(audio_path).exists():
        raise RuntimeError(f"OmniVoice voice cloning failed: {status_text}")

    audio_bytes = Path(audio_path).read_bytes()
    return audio_bytes, status_text


async def design_voice(
    text: str,
    lang: str = "Auto",
    gender: str = "Auto",
    age: str = "Auto",
    pitch: str = "Auto",
    accent: str = "Auto",
    duration: float = 5.0,
    steps: int = 32,
) -> tuple[bytes, str]:
    """Async entry point for OmniVoice Voice Design."""
    return await asyncio.to_thread(
        _voice_design_sync,
        text=text,
        lang=lang,
        gender=gender,
        age=age,
        pitch=pitch,
        accent=accent,
        duration=duration,
        steps=steps,
    )


async def clone_voice(
    text: str,
    ref_aud: str,
    ref_text: str,
    instruct: str = "Synthesize natural voice",
    lang: str = "Auto",
    duration: float = 5.0,
    steps: int = 32,
) -> tuple[bytes, str]:
    """Async entry point for OmniVoice Voice Cloning."""
    return await asyncio.to_thread(
        _voice_clone_sync,
        text=text,
        ref_aud=ref_aud,
        ref_text=ref_text,
        instruct=instruct,
        lang=lang,
        duration=duration,
        steps=steps,
    )
