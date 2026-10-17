"""Remote Kokoro adapter; local inference source lives in falai/kokoro."""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)
_kokoro_instances: dict = {}  # Import compatibility only; no local model is cached.


def _get_kokoro_model(*args, **kwargs):
    raise RuntimeError("Local Kokoro inference was relocated; worker is not configured.")


get_kokoro_model = _get_kokoro_model


def _generate_kokoro_local_cpu(*args, **kwargs):
    raise RuntimeError("Local Kokoro inference was relocated; worker is not configured.")


async def generate_kokoro_local(*args, **kwargs):
    raise RuntimeError("Local Kokoro inference was relocated; worker is not configured.")


VOICE_LANG_PREFIX_MAP = {
    "a": "en-us",  # American English
    "b": "en-gb",  # British English
    "e": "es",  # Spanish
    "f": "fr-fr",  # French
    "h": "hi",  # Hindi
    "i": "it",  # Italian
    "j": "ja",  # Japanese
    "p": "pt-br",  # Portuguese
    "z": "cmn",  # Mandarin
}


def _resolve_kokoro_lang(
    voice: str, lang: str | None = None, default_lang: str | None = None
) -> str:
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


async def _generate_kokoro_live(
    text: str,
    voice: str,
    speed: float = 1.0,
    lang: str = "en-us",
    model_name: str = "kokoro-v1.0-live",
    config: dict | None = None,
    url: str | None = None,
) -> tuple[bytes, str]:
    """Kokoro via Replicate; local fallback requires explicit DB configuration."""
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
                        output
                        if isinstance(output, str)
                        else (output[0] if isinstance(output, list) and output else None)
                    )
                    if audio_url:
                        audio_res = await client.get(audio_url)
                        if audio_res.status_code == 200:
                            return audio_res.content, "audio/wav"
                else:
                    logger.warning("Replicate returned HTTP %s.", res.status_code)
        except Exception as e:
            logger.warning(
                "Replicate Kokoro live generation failed (%s).",
                type(e).__name__,
            )

    if config.get("allow_local_fallback") is not True:
        raise RuntimeError(
            "Remote Kokoro generation unavailable; local fallback is disabled. "
            "Check remote provider availability and credentials."
        )

    wav_bytes, _ = await generate_kokoro_local(
        text=text, voice=voice, speed=speed, lang=lang, model_name=model_name, config=config
    )
    return wav_bytes, "audio/wav"
