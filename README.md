# AI Gateway & Search APIs

App authorization, gateway-owner permissions, migration details and Facebook isolation:
[APP_ACCESS.md](APP_ACCESS.md). App API requests now require a registered origin and
an app-bound key; frontend users remain separate from gateway owners.

### GenZee app provisioning

Sign in with the gateway's separate Google OAuth client first. Then, from this
repository, run the repeatable setup command with the existing backend owner:

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
.venv\Scripts\python.exe -m scripts.bootstrap_genzee_apps --owner-email bilal.azeemlab@gmail.com --frontend-root D:\voice_generator_free\free_voice_generator
```

The script enables this owner's API permission, approves local and production
registrations, grants the 14 routes configured for GenZee, and saves separate keys
in git-ignored frontend environment files. It preserves unrelated settings and
prints only nonsecret registration metadata. Re-running reuses valid saved keys.
Hosted production environment variables must still be installed on the existing
deployment and both services restarted/redeployed; writing local files does not
update a hosted deployment. See [APP_ACCESS.md](APP_ACCESS.md) for exact values
and the Facebook reconnect procedure.

Earlier gateway authorization verification: 123 isolated backend tests, 38 frontend ownership tests,
TypeScript and provisioning Ruff checks passed. Configured-database ASGI checks
accepted both registered apps, rejected missing/wrong/cross-environment credentials
with 401, rejected an ungranted endpoint with 403, and returned approved CORS
preflight 204. Hosted reachability and real Facebook publishing remain unverified.

A production-grade, multi-modal **AI Gateway** built with **FastAPI**, **SQLAlchemy** (async), **PostgreSQL / SQLite (WAL)**, and **Upstash Redis**. It provides unified multi-model LLM orchestration, intelligent hybrid routing, search aggregation, multi-modal media processing, and an extensible **Text-to-Speech (TTS)** subsystem.

---

## 🚀 Key Features

- **Multi-Modal Input Ingestion:** Audio transcription (Whisper STT), Vision analysis (GPT-4o), and Video processing.
- **Dynamic Text-to-Speech (TTS) Engine:**
  - **Modular Architecture (`app/services/tts/`):** Clean separation of providers, text chunkers, WAV mergers, disk caching, job queues, and notification webhooks.
  - **Backward-Compatible Facade:** `app/services/tts_service.py` re-exports all TTS utilities for legacy imports.
  - **Unified request flow:** Sample previews and background generation both use the same modular generator/providers. `app/services/tts/tts_service.py` is a compatibility import path, not a separate implementation.
  - **Shared retry policy:** Gemini uses up to 4 attempts (3 retries) per chunk, capped exponential backoff with jitter, transient 5xx/network retry, and DB key rotation on 429. Keys are sent in headers. NLTK/Punkt with Unicode fallback splits Gemini 2,500 / Kokoro 400 characters; request-start pacing defaults to 0.5 seconds.
  - **Job results:** Background processing records duration, size, object key and failure reason, and sends the existing signed result webhook when configured. Request-schema validation remains in place; the duplicate generator-only 20,000-character restriction was removed.
  - **Indexed checkpoints and retry:** App-owned jobs persist generation settings and private B2 chunk checkpoints. Failed/abandoned jobs resume only incomplete chunks; successful chunks merge in index order and are deleted after final completion. Default concurrency: Gemini 2/Kokoro 1; process-local pacing/limits and lease recovery apply. [TTS_CHECKPOINTS.md](TTS_CHECKPOINTS.md) documents migration, status/retry contracts and remaining limits.
  - **Checkpoint verification (2026-10-13):** 84 targeted isolated tests passed; changed-module Ruff, compilation and diff checks passed. Migration `e42b8d901c63` was applied and schema verified on the configured PostgreSQL DB; both existing GenZee apps have the retry grant. Gateway restart/redeploy remains required.
  - **Ordered live preview and logs (2026-10-13):** Authenticated NDJSON streams emit contiguous chunk indexes and resume with `after`. Final WAV frame metadata supports playback after temporary files are deleted. Selected voice settings stay unchanged on provider HTTP 400. Root JSONL logs seal at 1,000 records, receive UTC date-range filenames, and upload to private B2 with local retention on failure. See [TTS_CHECKPOINTS.md](TTS_CHECKPOINTS.md). 100 isolated backend tests passed; live provider/B2 delivery remains unverified.
  - **Unification verification (2026-10-13, prior phase):** 60 related TTS/audio/app-authorization regression tests passed with isolated SQLite and mocked external services; Ruff and compilation passed. Windows sandbox asyncio initialization stalled, so the selected isolated tests ran with approved execution outside the sandbox. Restart the running gateway to load these source changes; no production deployment was performed.
  - **Recovery and concurrency:** Eligible Gemini keys cycle; identical credentials allow two overlapping calls by default, configurable with `TTS_GEMINI_KEY_CONCURRENCY`. DB-empty fallback supports the environment key pool. Existing persisted leases/checkpoints support automatic expired-worker recovery with approved app/owner/grant checks and no quota-failure auto-retry. Hourly orphan cleanup preserves active/retry checkpoints and final files. No new migration. Frontend restores pending jobs, lists completed Studio/template audio by user and reconciles completion automatically. 117 isolated backend and 50 frontend ownership tests passed; production frontend build and browser fixtures passed. [TTS_TESTING.md](TTS_TESTING.md) contains manual verification and remaining live-provider checks.
  - **Providers Supported:**
    - Google Gemini 3.6 Flash TTS (with automatic DB-backed key rotation on 429 quota limits).
    - Kokoro-82M ONNX (Local CPU-bound inference & Replicate API fallback).
  - **Asynchronous Background Processing:** Long speech generation runs via FastAPI `BackgroundTasks`, uploads output WAVs to Backblaze B2, and notifies the client via HMAC-SHA256 signed webhooks.
  - **Voice Samples & Caching:** Instant preview audio samples with directory traversal protection.
- **Hybrid Intent Router:** Regex rule engine (fast path) + semantic vector cosine similarity classifier (slow path).
- **Search Aggregator:** Tavily, Brave Search, and DuckDuckGo with automatic fallback.
- **Resilient Caching:** Dual-layer cache with Upstash Redis (zlib compression) and SQLite/PostgreSQL caching.
- **Observability:** Sentry integration, request ID tracking, and structured JSON logs.

---

## 📁 Project Structure

```text
AIModels_SearchAPIS/
├── main.py                        # FastAPI entry point & lifespan handler
├── app/
│   ├── api/v1/                    # API routes (/query, /audio, /ingest, /media, /health)
│   │   ├── audio/router.py        # TTS endpoints: /models, /voices, /sample, /tts, etc.
│   │   └── ...
│   ├── core/                      # Config (pydantic-settings), auth, cache, logging
│   ├── db/                        # Database engine and session lifecycle
│   ├── models/db_models.py        # SQLAlchemy models (TTSJob, TTSModel, TTSVoice, etc.)
│   ├── services/
│   │   ├── tts_service.py         # TTS Facade (re-exports modular TTS system)
│   │   └── tts/                   # Modular TTS Subsystem
│   │       ├── constants.py       # Timeouts, limits, retries, sample directories
│   │       ├── text_utils.py      # Unicode/multilingual sentence chunking
│   │       ├── audio_utils.py     # WAV statistics (duration, size), PCM wrapping & merging
│   │       ├── sample_cache.py    # Voice sample cache with path traversal guard
│   │       ├── catalog.py         # TTS models & voices database queries
│   │       ├── gemini_provider.py # Gemini TTS client with key rotation
│   │       ├── kokoro_provider.py # Kokoro ONNX local & live Replicate runner
│   │       ├── generator.py       # Core dispatcher for TTS generation
│   │       ├── jobs.py            # Background job runner (B2 upload + DB state update)
│   │       └── notifier.py        # Webhook dispatcher (HMAC-SHA256)
│   └── ...
├── alembic/                       # Database migrations
├── static/                        # Audio samples & generated media
├── gateway.db                     # Local SQLite database (if used)
└── pyproject.toml                 # Dependencies & configuration
```

---

## 🛠️ Installation & Setup

### Prerequisites
- Python 3.12+
- `uv` (recommended) or `pip`

```bash
# Clone the repository
git clone https://github.com/bilalahmedkhatri/ai-api-s.git
cd ai-api-s

# Install dependencies with uv
uv sync
```

---

## ⚙️ Environment Variables

Create a `.env` file in the root directory:

```env
# Application
APP_NAME="AI Gateway"
DEBUG=false
HOST=0.0.0.0
PORT=8000

# Database (PostgreSQL or SQLite)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/gateway

# AI Models & Providers
GEMINI_API_KEY=AIzaSy...
GEMINI_API_KEYS=key1,key2,key3       # Optional pool for round-robin rotation
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...

# Backblaze B2 (Audio Storage)
B2_BUCKET_NAME=your-bucket
B2_APPLICATION_KEY_ID=your-key-id
B2_APPLICATION_KEY=your-app-key
B2_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com

# TTS Frontend Webhook (Optional)
FRONTEND_WEBHOOK_URL=https://your-frontend.com/api/tts/webhook
FRONTEND_WEBHOOK_SECRET=your-secret-key

# Upstash Redis (Optional Serverless Cache)
UPSTASH_REDIS_REST_URL=https://...upstash.io
UPSTASH_REDIS_REST_TOKEN=...
```

---

## 🗄️ Database Migrations

Apply Alembic migrations to bring the database schema up to date:

```bash
# Apply pending migrations
uv run alembic upgrade head

# Generate a new migration after model changes
uv run alembic revision --autogenerate -m "describe_changes"
```

### `TTSJob` Database Fields
The `tts_jobs` table tracks background audio generation:
- `job_id`: Unique identifier (UUID).
- `title`: Job description or truncated input text.
- `text`: Source text.
- `status`: `processing` | `completed` | `failed`.
- `audio_url`: Pre-signed temporary audio URL.
- `object_key`: Backblaze B2 object path (e.g. `tts_audio/tts_...wav`).
- `duration_sec`: Audio length in seconds.
- `size_bytes`: Audio file size in bytes.
- `error_message`: Error message if status is `failed`.

---

## 🔊 Text-to-Speech (TTS) API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/audio/models` | `GET` | List all active TTS models (Gemini, Kokoro, etc.) |
| `/api/v1/audio/voices` | `GET` | Paginated and searchable voices (`?model=...&search=...`) |
| `/api/v1/audio/sample` | `GET` | Instant audio preview for a selected model & voice |
| `/api/v1/audio/tts` | `POST` | Start async TTS job (`{ "model": "...", "text": "...", "voice": "..." }`) |
| `/api/v1/audio/tts/status/{job_id}` | `GET` | Query job status and retrieve generated audio URL |
| `/api/v1/audio/tts/renew-url` | `POST` | Renew expired B2 7-day pre-signed URL |

---

## 🏃 Running the Application

```bash
# Start the FastAPI server with hot-reload
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Access API Documentation:
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
