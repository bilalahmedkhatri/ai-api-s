"""app/services/tts/constants.py — shared tunables for the TTS package."""

import re
from pathlib import Path

# ── Gemini ───────────────────────────────────────────────────────────────────
GEMINI_TTS_TIMEOUT = 150.0          # seconds per request (long texts take 40-60 s)
GEMINI_TTS_MAX_RETRIES = 3          # transient errors only (timeout / 5xx / empty audio)
GEMINI_CHUNK_MAX_CHARS = 2500
GEMINI_INTER_CHUNK_DELAY = 0.5      # seconds, polite gap between chunk requests
GEMINI_BACKOFF_CAP = 20.0           # max wait (s) between retries
GEMINI_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})

# ── Kokoro ───────────────────────────────────────────────────────────────────
KOKORO_CHUNK_MAX_CHARS = 400
KOKORO_CHUNK_GAP_SEC = 0.2          # silence between chunks so speech doesn't "rush"

# ── Audio ────────────────────────────────────────────────────────────────────
DEFAULT_SAMPLE_RATE = 24000

# ── Sample cache ─────────────────────────────────────────────────────────────
# this file: <root>/app/services/tts/constants.py  ->  parents[3] == <root>
SAMPLES_DIR = Path(__file__).resolve().parents[3] / "static" / "gemini_samples"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
