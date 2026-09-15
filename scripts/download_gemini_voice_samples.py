"""scripts/download_gemini_voice_samples.py — Batch downloader for pre-generating Gemini TTS voice samples.

Batch & Rate-Limit Strategy:
  1. Scans models in order:
     - gemini-2.5-flash-preview-tts
     - gemini-3.1-flash-tts-preview
     - gemini-2.5-pro-preview-tts
     - gemini-2.5-flash-lite-tts-preview
  2. For each model, checks if static/gemini_samples/<model>/<voice>.wav already exists on disk.
  3. If existing, skips immediately (0 API calls).
  4. For missing voice samples, downloads up to BATCH_SIZE (default 4) files per execution.
  5. Includes DELAY_SECONDS (default 3.0s) between API calls to respect Google Free Tier limits (15 RPM).

Usage:
  uv run python scripts/download_gemini_voice_samples.py
  uv run python scripts/download_gemini_voice_samples.py --batch-size 5 --delay 4.0
"""

import argparse
import asyncio
import base64
import io
import logging
import os
import sys
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gemini_voice_downloader")

# Root path for saving audio samples
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "static" / "gemini_samples"

# Official Google Gemini TTS models & voices
OFFICIAL_MODELS = [
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-pro-preview-tts",
]

PREBUILT_VOICES = [
    {"name": "Puck", "sample_text": "Hello! I am Puck, an energetic and natural voice powered by Gemini."},
    {"name": "Charon", "sample_text": "Welcome. I am Charon, a deep and authoritative voice by Gemini."},
    {"name": "Kore", "sample_text": "Hello! I am Kore, a professional and clear female voice from Gemini."},
    {"name": "Fenrir", "sample_text": "Greetings! I am Fenrir, a bold and expressive Gemini voice."},
    {"name": "Aoede", "sample_text": "Hi there! I am Aoede, a warm and friendly voice powered by Gemini."},
    {"name": "Zephyr", "sample_text": "Hey! I am Zephyr, a bright and conversational female voice."},
    {"name": "Ursa", "sample_text": "Hello. I am Ursa, a soothing and calm female voice from Gemini."},
    {"name": "Orion", "sample_text": "Hello, I am Orion, a soft and articulate male voice."},
    {"name": "Pega", "sample_text": "Hi! I am Pega, a cheerful and lively female voice by Gemini."},
]


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, num_channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM bytes in a RIFF WAV container if not already WAV formatted."""
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


async def download_voice_sample(client: httpx.AsyncClient, api_key: str, model: str, voice_info: dict, dest_file: Path) -> bool:
    """Download a single voice sample from Gemini API and save to dest_file."""
    voice_name = voice_info["name"]
    text = voice_info["sample_text"]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name
                    }
                }
            },
        },
    }

    try:
        resp = await client.post(url, json=payload)
        if resp.status_code == 200:
            data = resp.json()
            part = data["candidates"][0]["content"]["parts"][0]
            inline_data = part.get("inlineData") or part.get("inline_data") or {}
            b64_data = inline_data["data"]
            raw_bytes = base64.b64decode(b64_data)
            wav_bytes = pcm_to_wav(raw_bytes)

            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_bytes(wav_bytes)
            logger.info("Successfully downloaded sample: model='%s' voice='%s' -> %s", model, voice_name, dest_file)
            return True
        else:
            logger.error("Failed to download voice='%s' model='%s' (HTTP %d): %s", voice_name, model, resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.error("Error downloading voice='%s' model='%s': %s", voice_name, model, exc)
        return False


async def run_batch_downloader(batch_size: int = 4, delay: float = 3.0):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not found in environment or .env file!")
        sys.exit(1)

    logger.info("Starting Gemini Voice Sample Batch Downloader (Batch Size: %d, Delay: %.1fs)", batch_size, delay)

    downloaded_count = 0
    total_existing = 0

    async with httpx.AsyncClient(timeout=35.0) as client:
        for model in OFFICIAL_MODELS:
            model_dir = SAMPLES_DIR / model
            model_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Scanning model: '%s'...", model)

            for voice_info in PREBUILT_VOICES:
                voice_name = voice_info["name"]
                dest_file = model_dir / f"{voice_name}.wav"

                if dest_file.exists() and dest_file.stat().st_size > 0:
                    total_existing += 1
                    logger.debug("Skipping existing sample: %s", dest_file)
                    continue

                # Voice sample is missing — download it!
                logger.info("[%d/%d] Fetching missing voice sample: model='%s', voice='%s'...", downloaded_count + 1, batch_size, model, voice_name)
                success = await download_voice_sample(client, api_key, model, voice_info, dest_file)

                if success:
                    downloaded_count += 1
                    if downloaded_count >= batch_size:
                        logger.info("Reached batch download limit (%d files). Stopping current run.", batch_size)
                        print(f"\n[SUMMARY] Downloaded {downloaded_count} new voice sample(s). Run the script again to fetch the next batch!")
                        return

                    # Pause between requests to strictly respect 15 RPM rate limits
                    logger.info("Pausing %.1f seconds to respect rate limits...", delay)
                    await asyncio.sleep(delay)

    total_possible = len(OFFICIAL_MODELS) * len(PREBUILT_VOICES)
    logger.info("Batch run complete. Downloaded: %d, Existing: %d, Total Available: %d", downloaded_count, total_existing, total_possible)
    if downloaded_count == 0 and total_existing >= total_possible:
        print("\n🎉 ALL VOICE SAMPLES ARE ALREADY DOWNLOADED! (100% Complete)")
    else:
        print(f"\n[SUMMARY] Processed batch run. Total downloaded so far: {total_existing + downloaded_count}/{total_possible}")


def main():
    parser = argparse.ArgumentParser(description="Gemini Voice Sample Batch Downloader")
    parser.add_argument("--batch-size", type=int, default=4, help="Maximum number of voice files to download per run (default: 4)")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay in seconds between requests (default: 3.0)")
    args = parser.parse_args()

    asyncio.run(run_batch_downloader(batch_size=args.batch_size, delay=args.delay))


if __name__ == "__main__":
    main()
