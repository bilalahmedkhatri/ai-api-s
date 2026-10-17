# AI Gateway & Search APIs

## Lightweight gateway status (2026-10-18)

- Removed gateway dependencies: `openai-whisper`, `kokoro-onnx`, `gradio-client`.
  `pyproject.toml` and `uv.lock` are updated; the gateway environment was synced.
  The lockfile pruned 36 package entries without upgrading retained versions.
- Retained: SoundFile, explicit NumPy (`numpy>=2.4.6`), LiteLLM, NLTK and the
  B2 SDKs (`aioboto3`, `boto3`, `botocore`). NumPy still powers semantic routing.
- Removed routes: `POST /api/v1/ingest/audio/` and
  `POST /api/v1/ingest/audio/url`; both return 404 and are absent from OpenAPI.
- Local Kokoro is hidden from the public model/voice catalog and rejected before
  new jobs, sample cache access, manual retries and automatic recovery. Existing
  DB rows and job snapshots were preserved; do not rewrite ownership or history.
- Gemini and remote Kokoro remain supported. Existing B2 audio, job status,
  streaming and signed-URL renewal remain available through their authorized flows.
- Original Whisper/Kokoro/OmniVoice sources and Kokoro assets live in
  `D:/voice_generator_free/falai/{whisper,kokoro,omnivoice}`. Workers require their
  own environments; no worker transport or fal.ai deployment exists yet. Do not
  import sibling checkout files or restore local inference on the gateway.
- Verification after dependency cleanup: **333 isolated backend tests passed**;
  gateway/retained-library imports and `uv lock --check --offline` passed.
  Providers/storage were mocked; hosted Linux RAM, real synthesis and production
  deployment were not verified. No Git push or deployment was performed.

See [INFERENCE_RELOCATION.md](INFERENCE_RELOCATION.md) for scope and worker boundaries.

### Deferred customer API-key controls

The current customer key flow grants each new customer project all available app endpoints; plan-based endpoint permissions and per-user usage quotas are not enforced here. There is also no limit on the number of active customer keys (issuance is rate-limited). These two control areas are intentionally deferred for later work; generation, ownership and revocation are already implemented. Do not describe the current flow as plan/quota enforced.

### Query request records API

Authenticate using an active app API key (`X-API-Key` or Bearer) with the matching GET grants:

- `GET /api/v1/query/requests?page=1&page_size=20` returns `page`, `page_size`, `total`, and `items`. Page size is capped at 100; optional `status=completed|failed|processing|cancelled|rejected` filters records using the canonical saved request status.
- `GET /api/v1/query/requests/{query_id}` returns one record plus its normalized search sources. Foreign-app, legacy-unassigned and missing IDs return 404. Responses use no-store.

Each record includes input, answer, creation time, model_used, input_tokens, output_tokens, total_tokens, cost_usd, status, duration_ms, request metadata and routing metadata. Request metadata contains the request ID and app-key ID (never the secret); routing contains per-model statuses, failure types/status codes, per-attempt timing, cache flags, intent, and semantic/search/LLM timings when those stages complete. Cache hits receive their own final-response record and zero new chat tokens/cost. Unknown provider tokens/cost remain null; failed attempts, embeddings and search fees are not included in the successful-chat cost estimate. `cost_usd` remains the successful chat estimate; request metadata now separately reports observed provider calls and their cost coverage (see reliability below). Duration measures the authenticated processing handler, excluding authentication and final HTTP serialization. Pre-handler authentication/validation rejections now have sanitized query audit rows; unverified identities stay unassigned and rejected bodies are omitted.

Reuses queries, final_responses, search_results, and model_website_usages. Query metadata is now populated; daily model usage uses `app:<authorized_app_id>` rather than a shared label. DB/Redis query caches are app-scoped. Unexpected early pipeline failures are recorded with sanitized error types and a generic 503, provided the database remains available. Legacy records are never assigned automatically.

Deployment: migration `d920f41a672e` adds nullable indexed `queries.authorized_app_id` with an app foreign key. It also appends the two GET grants only to apps already granted `POST /api/v1/query/`; unrelated grants are preserved. It does not grant query generation to additional apps. Configured preflight at revision c84f10d2a639 found only app ID 3 needing those grants. **Configured migration applied on 2026-10-16 after explicit user approval:** final revision d920f41a672e, ownership column/index/foreign key, and exactly app 3's two additive GET grants were verified. Unrelated app grants remained unchanged and legacy query rows remained unassigned. Restart/redeploy the backend to serve the new endpoints. Migration downgrade removes the ownership column/index/constraint but intentionally leaves additive endpoint grants unchanged; authorization still requires a registered matching route.

Verification: 124 combined isolated regressions passed; the subsequently refined migration/grant test also passed (four history tests), alongside targeted Ruff and compilation. Regression providers were mocked. Configured schema and the approved app 3 read grants were subsequently migrated and verified separately; no real generation, secret fetch or private request-content inspection was performed for this feature.

### Query reliability and admin history (2026-10-17)

- Provider telemetry is request-local and records each observed chat attempt, embedding call (including centroid initialization) and search fallback, with sanitized status/type, duration, available tokens and estimated cost. Failed-attempt provider usage never enters user token totals. Missing provider usage/pricing stays null, not zero. Request metadata exposes `provider_calls`, `known_provider_cost_usd`, `total_provider_cost_usd`, `other_provider_cost_usd`, `unknown_cost_count`, `provider_cost_complete` and `cost_scope=observed_provider_estimates`. The total is null whenever coverage is incomplete. These are estimates, not a reconciled provider invoice. Cache hits perform no new provider calls and keep zero new chat cost/tokens.
- Optional account-specific search estimates: `TAVILY_SEARCH_COST_USD`, `BRAVE_SEARCH_COST_USD`, `DUCKDUCKGO_SEARCH_COST_USD` (nonnegative USD per successful call). Leave unset when unknown. Failed searches remain unknown because billing policy can differ. LiteLLM's local pricing estimates chat/embedding usage when supported. Existing historical costs are not invented or backfilled.
- Rejected POST query requests receive `status=rejected`, HTTP status, request ID and duration without storing rejected bodies, credentials or arbitrary error text. Only a verified active key supplies app ownership; unauthenticated rejections remain unassigned and visible only to admins. App history still requires its exact GET grants and cannot see another app or unassigned records. HTTP JSONL logs now include status codes. Rejection auditing is best-effort during database failure; logs remain the fallback. No preflight or GET polling audit rows are created.
- Generation processing has a 180-second deadline. A lifespan maintenance task starts immediately and runs every 30 seconds. In bounded batches of 100 it marks processing rows older than 10 minutes failed (`WorkerInterrupted`), aligns final-response metadata, flags incomplete cost coverage, and never reruns generation or charges failed requests. Database outages defer cleanup until connectivity returns. This recovers record state, not a lost provider response.
- Successful requests durably mark accounting pending before aggregation. A compare-and-set claim, the existing atomic usage upsert and the recorded marker share one database transaction. Maintenance retries pending completed requests using the request's original UTC date; failed/cancelled/cache requests are excluded. Deferred rows rotate so bad records do not starve newer entries. No automatic replay of historical recorded or unmarked rows.
- Admin `/requests` reuses existing table, badge, card, error and loading UI, with pagination, status filtering and selectable request details (input/answer, tokens, timings, model attempts, provider estimates and sources). Remix loaders use server-only backend calls and no-store responses. Backend `GET /api/v1/dashboard/query-requests` and `GET /api/v1/dashboard/query-requests/{query_id}` independently require an approved administrator. Ordinary customer/app keys cannot use these routes. The app-scoped APIs remain unchanged apart from additive metadata and the rejected filter.
- Verification: 182 isolated backend regressions and 20 frontend mock-server tests passed; TypeScript, production build, targeted Ruff/compilation and diff checks passed. A 24-call replay across independent SQLite sessions counted usage exactly once. Mock-backed browser checks passed at 1440px, 768px and 390px, including detail scrolling, failed filtering and pagination. No configured database or live provider/invoice was used.
- Reuses existing query/final/usage metadata; no new tables, schema migration, secret generation or grant changes. Restart/redeploy backend and admin to activate. Real provider invoice matching and production PostgreSQL concurrency are separate from isolated SQLite/mocked regressions.

### Query medium-priority fixes (2026-10-17)

Cancellation during query processing is explicitly recorded as cancelled with internal HTTP classification 499; cleanup is shielded from cooperative cancellation. Cancelled/failed requests do not enter user token totals. If a response already committed before a later processing error, both request and final metadata are marked consistently; history status filters use Query metadata rather than inferring status from a model-name sentinel. The filter accepts completed, failed, processing and cancelled. A hard process kill/database outage can still prevent immediate finalization; the query maintenance loop now finalizes stale processing rows after recovery. Cancellation classification does not imply an HTTP 499 response is delivered to a disconnected client.

Greeting and thanks rules return direct useful replies at zero chat tokens/cost. Time returns a fresh, explicitly UTC ISO-8601 timestamp; both caches are bypassed and time answers are not cached. Weather, pricing, news and factual-person rules now use the existing search-backed async chat flow. When those searches yield no results, return an explicit unavailable message rather than inventing current facts, make no chat call and do not reuse that negative answer from either cache. Legacy rule-placeholder cache entries are ignored.

DB cache hits restore the original model, intent, search engine and normalized sources. Both DB and Redis cache paths persist those sources for the new request, so response/history stay consistent across repeated hits. Cache hits still have zero new chat tokens/cost and no user usage increment. Reuses existing SearchResult and FinalResponse tables; no schema migration or endpoint grants are needed.

Medium-fix verification: 152 targeted isolated regressions passed, plus targeted Ruff, compilation and diff checks. Coverage includes cancellation with no user charge, post-commit failure/filter consistency, greeting/thanks/time answers, all four search-backed rule categories, repeated DB cache source copying, Redis source history, and uncached search-unavailable responses. Services were mocked; no live provider/browser test or configured data/schema change was performed.

### Query fallback policy (2026-10-17)

The first candidate retains the preferred canonical model/free/combined-price/ID ranking. Active DB model `replicate/openai/gpt-4o-mini` is inserted immediately after that first candidate (when multiple candidates exist); its DB name can remain `openai/gpt-4o-mini` with provider `replicate`. Query routing excludes OpenRouter entirely, even when it has active models/keys. The existing separate vision helper is unchanged. Calls continue through the shared LiteLLM `acompletion` and the matching provider's DB key; no provider-specific SDK is added. The hosted model is listed in Replicate's official reference: https://replicate.com/openai/gpt-4o-mini/api/api-reference . Its known base GPT chat metadata is reused for compatibility filtering; unknown unrelated models still fail closed.

The selector no longer truncates before key eligibility. The shared execution loop skips missing/exhausted/corrupt key pools, atomically reserves only the next usable provider call, and stops after at most four actual network calls. Skips remain in model_attempts for diagnosis but do not count against that four-call budget. Read-only preflight found active Replicate GPT model ID 318 and one eligible Replicate key; no live generation was attempted. Old direct-OpenAI key/mapping blockers below were historical observations, not a requirement to change this explicitly selected Replicate-hosted model.

### Query quota and accounting fixes

Both high-priority bugs were reproduced by failing regressions before changes. Query key selection now reuses get_next_available_key(reserve=True): a conditional database UPDATE rechecks active/quota state and increments requests_today before network I/O. Concurrent callers cannot reserve the same final slot; unsuccessful conditional updates try the remaining keys. No row lock is held during network I/O. Success only updates last_used_at; total_count is unchanged. A failed/cancelled/crashed attempt conservatively keeps its provider-request slot; this is not a user token charge. Other key-manager callers keep their existing default behavior.

User usage aggregation now runs only after a successful query and its answer/tokens/cost/request status have committed. Failed model attempts and wholly failed requests never enter user totals, even if the provider reports token consumption. Cache hits add no new user tokens. Low-level call_llm does not charge users. Last-use bookkeeping failure cannot discard a successful provider result; usage accounting failure returns the successful answer and retains its detailed FinalResponse, marking usage_accounting_status=pending with a sanitized error type when the database is available. Pending accounting now retries automatically using the durable request/final-response record; its recorded marker and usage upsert commit atomically to prevent double counting. Daily usage uses atomic PostgreSQL/SQLite upserts to avoid first-row insert races. HTTP delivery itself cannot be transacted with the database; this change bills completed generation, not client receipt.

Verification: 139 isolated regressions passed, including separate-session/thread SQLite reservations (24 contenders, three available slots), overlapping chat calls with one remaining slot, concurrent daily upserts, bookkeeping failure survival, failed-attempt/failed-request token exclusion, and PostgreSQL conditional-UPDATE compilation. Targeted Ruff, compilation and diff checks passed. No configured schema or data was changed and no real provider call or live PostgreSQL concurrency test was performed. That verification preceded the fallback-policy update below, which fixes the third issue by limiting actual provider calls rather than pre-truncating candidates.

### Query verification (2026-10-16)

DB routing follow-up: `call_llm(prompt, intent, db=..., force_model="openai/gpt-4o-mini")` now selects active DB chat models using local LiteLLM capability metadata, preferring that canonical model name and then free/lowest-price/ID order. Unknown capabilities are excluded except that the explicitly requested Replicate-hosted GPT-4o-mini uses its known base GPT chat capability. At most four actual provider calls are made; models without an eligible key are skipped without consuming that budget. Each candidate gets its own provider's eligible DB key through the existing key manager and awaits `acompletion` with retries disabled. No environment-key fallback is used. Attempts record sanitized status/error type/timing; successful actual model, tokens and cost are persisted by the existing final-response path, and selected-model usage uses existing pricing metadata. Query calls atomically reserve requests_today before provider I/O; success updates last_used_at without counting twice, and 429 invokes existing quota handling. These provider-attempt slots are separate from user token accounting; failed attempts consume no user tokens. total_count retains its separate allocation-endpoint semantics. Existing TTS/keyword callers retain their prior counter flow; intent-specific DB model priorities remain future work. All-provider failure returns 503, failed/legacy-error responses are excluded from caches, and no-cache also bypasses the DB rule cache. Direct rule responses and search-backed rule answers are implemented; see the medium-priority follow-up below.

Configured-data preflight found a blocker for the requested live OpenAI test: model row 318 is named openai/gpt-4o-mini but its provider is replicate, and there are zero active, under-quota openai provider key rows. No configured rows or secrets were modified during this read-only check. Correct the model/provider mapping and provision a valid OpenAI key through the existing management flow before claiming real OpenAI generation works.

DB-routing validation: 114 isolated query/async/authentication/usage/customer-key/provider-key tests passed, plus targeted Ruff, compilation and diff checks. Coverage includes overlapping awaits, active/chat filtering, provider-specific keys, explicit fallback, 429 quota handling, missing-key fail-closed behavior, actual-model/token persistence, nonduplicated usage, repeated-failure cache exclusion and no-cache success regeneration. Providers were mocked; live OpenAI generation remains blocked by the configured-data preflight above.

Earlier async-only follow-up: the query LLM call now awaits the existing LiteLLM `acompletion`, and semantic embeddings await `aembedding`. Model selection, fallback configuration and response contracts remain unchanged. Vision's separate synchronous helper is outside this query-only change. Provider readiness and the failure/cache/placeholder issues below remain unresolved by this async change.

Manual ASGI requests used real app-key verification against an isolated in-memory database, shared-cache writes disabled, and real configured providers. Missing credentials returned 401; greetings returned an internal rule placeholder. Math and letter queries reached the LLM stage but returned HTTP 200 with an LLM-error answer; the letter query completed semantic classification and Tavily search. Direct tiny provider probes returned Groq Qwen model_decommissioned (400), Groq Llama 70B model_not_found/access unavailable (404), and OpenAI invalid credentials (401). No successful real generation was verified. A separate mocked-failure endpoint reproduction confirmed that a repeated query reuses the persisted error through the DB rule cache even with Cache-Control: no-cache. Existing query/routing/authorization regressions: 78 passed; these do not prove provider readiness. Those initial diagnostics preceded the DB-routing follow-up above. Provider readiness still needs live validation. Rule placeholders, failure status, failed-answer cache exclusion and consistent cache bypass are now addressed. No configured-database rows or shared Redis entries were written by these diagnostics.

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

- **Multi-Modal Input Ingestion:** Vision analysis (GPT-4o) and video keyframe processing. Whisper transcription is moved out of this gateway.
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
    - Remote Kokoro through the existing Replicate-style HTTP adapter. Local ONNX inference and local fallback are unavailable on this gateway.
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
│   │       ├── kokoro_provider.py # Remote Kokoro HTTP adapter; local compatibility calls reject
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

The existing Dockerfile and CI workflow still target Python 3.12; align them with the declared Python 3.14 requirement before using those paths. They were not changed or validated by the dependency cleanup.

- Python 3.14+ (declared in `pyproject.toml` and `.python-version`)
- `uv` (recommended) or `pip`

```bash
# Clone the repository
git clone https://github.com/bilalahmedkhatri/ai-api-s.git
cd ai-api-s

# Install the locked production and development dependencies
uv sync --locked --all-groups
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
| `/api/v1/audio/models` | `GET` | List available active TTS models; local Kokoro excluded |
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
