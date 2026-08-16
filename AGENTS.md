# AI Gateway - Complete System Reference

> **Project:** AIModels_SearchAPIS
> **Runtime:** Python 3.12+ - FastAPI - SQLite (WAL) - Upstash Redis
> **Package Manager:** uv
> **Test Suite:** 46 tests across 5 phases - all passing

---

## Table of Contents

1. [What We Built](#1-what-we-built)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Full Request Pipeline](#3-full-request-pipeline)
4. [Directory Structure](#4-directory-structure)
5. [AI Models and Providers](#5-ai-models-and-providers)
6. [Search Engine Aggregator](#6-search-engine-aggregator)
7. [Caching Layer - Upstash Redis](#7-caching-layer---upstash-redis)
8. [Database Schema](#8-database-schema)
9. [API Endpoints Reference](#9-api-endpoints-reference)
10. [Configuration and Environment Variables](#10-configuration-and-environment-variables)
11. [Observability and Logging](#11-observability-and-logging)
12. [Deployment Architecture](#12-deployment-architecture)
13. [CICD Pipeline](#13-cicd-pipeline)
14. [Running the App](#14-running-the-app)
15. [Testing](#15-testing)
16. [Model Upgrade Policy](#16-model-upgrade-policy)

---

## 1. What We Built

This project is a **production-grade AI Gateway** - a unified, multi-modal API server that acts as an
intelligent routing layer in front of multiple LLM providers, search engines, and processing pipelines.

### Core Capabilities

| Capability | Implementation |
|---|---|
| Multi-modal input ingestion | Audio (Whisper STT), Image (GPT-4o Vision), Video (ffmpeg + Vision) |
| Hybrid intent routing | Regex rule engine + semantic cosine similarity classifier |
| Search aggregation | Tavily (AI search), Brave Search, DuckDuckGo (free fallback) |
| RAG pipeline | Search results injected as context into LLM prompt |
| 30B+ model orchestration | Qwen-2.5-Coder-32B, Llama-3.3-70B, Command-R+, Nemotron-550B |
| Serverless caching | Upstash Redis (HTTP) + zlib compression, dynamic TTL |
| Observability | Sentry SDK, structured JSON logs, /api/v1/health/ deep check |
| Production deployment | Docker multi-stage, Litestream WAL to S3, GitHub Actions CI/CD |

---

## 2. System Architecture Overview

```
+------------------------------------------------------------------+
|                        CLIENT REQUEST                            |
|         (text query / audio file / image / video)                |
+----------------------------------+-------------------------------+
                                   |  HTTP
                                   v
+------------------------------------------------------------------+
|              FastAPI  (Uvicorn ASGI, port 8000)                  |
|  +------------------------------------------------------------+  |
|  |  RequestIDMiddleware  +  CORS  +  Sentry FastAPI Plugin    |  |
|  +------------------------------------------------------------+  |
|                                                                  |
|   /api/v1/ingest/audio   -->  Whisper STT  --> text pipeline    |
|   /api/v1/ingest/image   -->  GPT-4o Vision description         |
|   /api/v1/ingest/video   -->  ffmpeg frames + GPT-4o Vision     |
|   /api/v1/query/         -->  Full 8-stage pipeline (below)     |
|   /api/v1/health/        -->  Deep health check (DB + Redis)    |
|   /health                -->  Shallow heartbeat                  |
+----------------------------------+-------------------------------+
                                   |
          +------------------------+------------------+
          v                        v                  v
  +--------------+      +------------------+    +-----------+
  |  Upstash     |      | SQLite (WAL mode)|    | Sentry.io |
  |  Redis       |      |  gateway.db      |    |  Error    |
  |  (HTTP/TLS)  |      |  + Litestream S3 |    |  Tracking |
  +--------------+      +------------------+    +-----------+
          |
          |  Cache miss - Live pipeline
          v
  +--------------------------------------------------------------+
  |              HYBRID INTENT ROUTER                            |
  |  Step 1: Regex Rule Engine (9 patterns, SQLite 1hr cache)    |
  |  Step 2: Semantic Router (cosine similarity, numpy)          |
  +-------------------------------+------------------------------+
                                  |
           +----------------------+------------------+
           v                      v                  v
   +-------------+       +------------+      +--------------+
   |  Tavily     |       |  Brave     |      |  DuckDuckGo  |
   |  AI Search  |       |  Search    |      |  (free)      |
   +-------------+       +------------+      +--------------+
           |  UnifiedSearchResponse (Pydantic v2)
           v
  +--------------------------------------------------------------+
  |               MODEL ROUTING MATRIX                           |
  |   coding/math  --> Qwen-2.5-Coder-32B (Groq)                |
  |   reasoning    --> Llama-3.3-70B (Groq)                      |
  |   RAG          --> Command-R+ (Cohere) / Llama-3.3-70B       |
  |   fallback     --> Nemotron-550B (OpenRouter, free tier)     |
  |   timeout=15s, litellm native fallback chain                 |
  +--------------------------------------------------------------+
           |
           v
  FinalResponse persisted (tokens + cost + telemetry) --> Upstash cache
```

---

## 3. Full Request Pipeline

Every call to `POST /api/v1/query/` runs through **8 sequential stages**:

```
Stage 1: Upstash Redis GET(MD5 key)
         |-- HIT  --> decompress --> return immediately (under 5ms)
         |-- MISS or Cache-Control: no-cache --> continue

Stage 2: Persist Query row to SQLite (queries table)
         |-- flush() populates query_id for downstream linkage

Stage 3: Rule Engine - fast path
         |-- SQLite 1-hour MD5 cache check (FinalResponse table)
         |-- 9 regex patterns: weather, pricing, time, news,
         |       factual_person, greeting, thanks, math_expression, math_task
         |-- HIT --> return; NO MATCH --> continue to Stage 4

Stage 4: Semantic Router - slow path
         |-- Embed query via litellm (text-embedding-3-small)
         |-- Cosine similarity against 5 intent centroids (numpy)
         |-- Returns: category + model_hint + similarity score

Stage 5: Search Aggregation (if requires_search=True)
         |-- Auto-select: Tavily --> Brave --> DuckDuckGo (graceful fallback)
         |-- Returns UnifiedSearchResponse (title, url, snippet, score, engine)
         |-- Persists raw hits to search_results table

Stage 6: LLM Call via Model Router
         |-- RAG prompt built (query + up to 5 search snippets)
         |-- primary model selected by ROUTING_MATRIX[intent]
         |-- litellm.completion(fallbacks=[...], timeout=15)
         |-- Extracts: answer, prompt_tokens, completion_tokens, cost_usd

Stage 7: Persist FinalResponse
         |-- Writes: generated_output, model_used, prompt_tokens,
         |       completion_tokens, cost_usd, meta_fields (full routing
         |       telemetry including query_hash for SQLite cache lookup)

Stage 8: Upstash Redis SET
         |-- TTL = 7 days  for: coding, mathematics, general_knowledge
         |-- TTL = 1 hour  for: search-backed, real-time, general
```

---

## 4. Directory Structure

```
AIModels_SearchAPIS/
|
|-- main.py                         # FastAPI app entry point + Sentry init
|-- Dockerfile                      # Multi-stage: uv builder --> slim runtime
|-- entrypoint.sh                   # Litestream replicate --> Uvicorn (conditional)
|-- docker-compose.yml              # Named SQLite volume + .env passthrough
|-- pyproject.toml                  # All deps + ruff + pytest config
|-- alembic.ini                     # Alembic async migration config
|-- MODELS.md                       # 30B+ model roster + quarterly upgrade SOP
|-- AGENTS.md                       # THIS FILE: complete system reference
|
|-- alembic/                        # DB migration scripts (async SQLite)
|   |-- env.py                      # Async migration runner
|
|-- .github/workflows/
|   |-- ci.yml                      # push/PR --> ruff check + pytest (46 tests)
|   |-- deploy.yml                  # CI pass --> Docker build + push + rolling restart
|
|-- scripts/
|   |-- archive_logs.py             # gzip archiver, cron-ready, --dry-run safe
|
|-- app/
|   |-- core/                       # Cross-cutting concerns
|   |   |-- config.py               # Pydantic v2 BaseSettings (all env vars)
|   |   |-- cache.py                # Upstash Redis singleton + zlib compression
|   |   |-- logging_config.py       # JSON rotating file logs (stdlib only)
|   |   |-- middleware.py           # RequestIDMiddleware (UUID per request)
|   |
|   |-- db/
|   |   |-- database.py             # Async SQLAlchemy engine, WAL PRAGMA, get_db()
|   |
|   |-- models/
|   |   |-- db_models.py            # ORM: Query, SearchResult, FinalResponse
|   |
|   |-- api/
|   |   |-- v1/
|   |       |-- router.py           # All /api/v1/* routes aggregated
|   |       |-- health.py           # GET /api/v1/health/ (DB + Upstash deep check)
|   |       |-- ingest/
|   |       |   |-- audio.py        # POST /api/v1/ingest/audio/ (Whisper STT)
|   |       |   |-- image.py        # POST /api/v1/ingest/image/ (Vision LLM)
|   |       |   |-- video.py        # POST /api/v1/ingest/video/ (ffmpeg + Vision)
|   |       |   |-- schemas.py      # IngestResponse Pydantic models
|   |       |-- query/
|   |           |-- router.py       # POST /api/v1/query/ (full pipeline)
|   |           |-- schemas.py      # QueryRequest/Response, RoutingDecision
|   |
|   |-- services/                   # Core business logic
|   |   |-- dispatcher.py           # 8-stage pipeline orchestrator
|   |   |-- rule_engine.py          # Regex rules + SQLite 1hr MD5 cache
|   |   |-- semantic_router.py      # Cosine similarity intent classifier
|   |   |-- model_router.py         # 30B+ routing matrix + call_llm()
|   |   |-- search_aggregator.py    # Tavily + Brave + DuckDuckGo clients
|   |   |-- embedding.py            # litellm.embedding() wrapper (lazy-cached)
|   |
|   |-- search/                     # Search engine module stubs (user-extended)
|   |   |-- tavily_search.py
|   |   |-- brave_search.py
|   |   |-- duckduckgo_search.py
|   |
|   |-- processing/                 # Data processing utilities (user-extended)
|   |   |-- data_compiler.py
|   |   |-- embedding_processor.py
|   |
|   |-- routers/                    # Additional router stubs (user-extended)
|
|-- tests/
    |-- test_db_init.py             # Phase 1: DB init + WAL mode
    |-- test_ingest_routes.py       # Phase 2: Audio/image route registration
    |-- test_phase3.py              # Phase 3: Rule engine + cosine math + schemas
    |-- test_phase4.py              # Phase 4: Cache compression + routing matrix
    |-- test_phase5.py              # Phase 5: Health endpoint + log archiver
```

---

## 5. AI Models and Providers

### 5.1 Model Routing Matrix

Intent categories (from Phase 3 semantic router) map to specific 30B+ models via ROUTING_MATRIX in
app/services/model_router.py:

| Intent Category | Primary Model | Provider | Fallback Chain |
|---|---|---|---|
| coding | qwen-2.5-coder-32b | **Groq** | Llama-3.3-70B --> Llama-3.1-8B |
| mathematics | qwen-2.5-coder-32b | **Groq** | Llama-3.3-70B --> Llama-3.1-8B |
| math_task | qwen-2.5-coder-32b | **Groq** | Llama-3.3-70B |
| math_expression | qwen-2.5-coder-32b | **Groq** | Llama-3.3-70B |
| creative_writing | llama-3.3-70b-versatile | **Groq** | Llama-3.1-8B --> Command-R+ |
| general_knowledge | llama-3.3-70b-versatile | **Groq** | Command-R+ --> Llama-3.1-8B |
| general (default) | llama-3.3-70b-versatile | **Groq** | Llama-3.1-8B --> Nemotron-550B |
| Last-resort fallback | nvidia/nemotron-3-ultra-550b | **OpenRouter** (free) | -- |

### 5.2 Embedding Model

| Purpose | Model | Provider |
|---|---|---|
| Query embedding + intent centroid building | text-embedding-3-small | OpenAI via litellm |

Override via EMBEDDING_MODEL env var. Any litellm-compatible embedding model works.

### 5.3 Multi-Modal Models

| Input Type | Model | Provider | Endpoint |
|---|---|---|---|
| Audio (STT) | openai/whisper-1 | OpenAI Whisper | /api/v1/ingest/audio/ |
| Image (Vision) | gpt-4o | OpenAI | /api/v1/ingest/image/ |
| Video (frames + Vision) | gpt-4o | OpenAI via litellm | /api/v1/ingest/video/ |

### 5.4 LiteLLM - The Abstraction Layer

All LLM calls go through LiteLLM (litellm>=1.96.0), which provides:

- **Unified API** - identical call signature regardless of provider
- **Native fallbacks** - fallbacks=[{model, messages}] auto-retries on failure or rate-limit
- **Timeout** - timeout=15 seconds hard deadline, then triggers fallback chain
- **Cost tracking** - litellm.completion_cost(response) returns USD cost per call
- **Token counting** - response.usage.prompt_tokens / completion_tokens extracted automatically

### 5.5 Provider API Keys Required

| Provider | Purpose | Env Var |
|---|---|---|
| **OpenAI** | Whisper STT, GPT-4o Vision, Embeddings | OPENAI_API_KEY |
| **Groq** | Qwen-2.5-Coder-32B, Llama-3.3-70B, Llama-3.1-8B | GROQ_API_KEY |
| **Cohere** | Command-R+ (RAG fallback) | COHERE_API_KEY |
| **OpenRouter** | Nemotron-550B free tier (last-resort fallback) | OPENROUTER_API_KEY |

---

## 6. Search Engine Aggregator

Three search engine clients with a graceful auto-fallback chain:

Auto mode: Tavily --> Brave --> DuckDuckGo

| Engine | Type | Requires Key | Best For |
|---|---|---|---|
| **Tavily** | AI-native search | Yes - TAVILY_API_KEY | Multi-hop factual queries, high-quality snippets |
| **Brave Search** | Privacy-first web | Yes - BRAVE_API_KEY | General web + news aggregation |
| **DuckDuckGo** | Web search | No - Free | Fallback - always available |

All three engines return a normalised UnifiedSearchResponse (Pydantic v2):
- Fields: title, url, snippet (max 500 chars), relevance_score (0.0 to 1.0), engine name

Up to 5 results are fetched per query. Each result is persisted to the search_results table linked
by query_id for full audit trail.

---

## 7. Caching Layer - Upstash Redis

### Architecture

Upstash provides serverless, HTTP-based Redis. No persistent TCP socket. No server to manage.
Perfect for FastAPI on cloud platforms (Render, DigitalOcean, Fly.io, AWS).

Cache key format: gw:v1:{md5(query.strip().lower())}

| Feature | Detail |
|---|---|
| Transport | HTTPS REST API via upstash-redis>=1.7.0 |
| Compression | zlib level-6, applied to payloads over 512 bytes |
| Compression sentinel | custom prefix detects compressed vs raw payloads |
| Fail-open | If credentials absent, _NoOpCache silently no-ops - never breaks pipeline |
| Cache bypass | Cache-Control: no-cache header forces fresh response, still writes to cache after |

### TTL Strategy

| Intent Category | TTL | Rationale |
|---|---|---|
| coding, mathematics, general_knowledge, math_task, math_expression | 7 days (604800s) | Stable factual answers - rarely change |
| All other categories (search-backed, real-time, general, creative) | 1 hour (3600s) | Web data may become stale |

### Dual-Cache Architecture

The system has two independent cache layers:

1. **Upstash Redis** (Stage 1) - checked first, full response payload stored with zlib compression
2. **SQLite MD5 cache** (Stage 3) - 1-hour TTL lookup in FinalResponse table via query_hash in meta_fields

---

## 8. Database Schema

- **Engine:** SQLite with WAL (Write-Ahead Logging) mode for concurrency safety
- **ORM:** SQLAlchemy 2.0 async with aiosqlite driver
- **Migrations:** Alembic async migrations initialized with alembic init -t async

### Table: queries

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment primary key |
| raw_query | TEXT | Original user query string |
| input_type | VARCHAR(64) | "text", "voice", or "image" |
| created_at | DATETIME(TZ) | UTC creation timestamp |
| meta_fields | TEXT JSON | Flexible extra context as JSON |

### Table: search_results

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment primary key |
| query_id | INTEGER indexed | Foreign key to queries.id |
| engine_name | VARCHAR(64) | "tavily", "brave", or "duckduckgo" |
| raw_response | TEXT JSON | Full UnifiedSearchResult dict as JSON |
| created_at | DATETIME(TZ) | UTC creation timestamp |

### Table: final_responses

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment primary key |
| query_id | INTEGER indexed | Foreign key to queries.id |
| generated_output | TEXT | LLM answer text |
| model_used | VARCHAR(128) | Full model identifier e.g. groq/llama-3.3-70b-versatile |
| prompt_tokens | INTEGER | Token count for the prompt |
| completion_tokens | INTEGER | Token count for the response |
| cost_usd | FLOAT | Estimated USD cost via litellm.completion_cost() |
| created_at | DATETIME(TZ) | UTC creation timestamp |
| meta_fields | TEXT JSON | Full RoutingDecision dict + query_hash |

### meta_fields JSON Schema (FinalResponse example)

```json
{
  "rule_matched": false,
  "rule_pattern": null,
  "intent_category": "coding",
  "intent_score": 0.87,
  "requires_search": false,
  "requires_llm": true,
  "cache_hit": false,
  "engine_used": "tavily",
  "model_used": "groq/qwen-2.5-coder-32b",
  "elapsed_ms": 1234.5,
  "query_hash": "5d41402abc4b2a76b9719d911..."
}
```

---

## 9. API Endpoints Reference

- **Base URL:** http://localhost:8000
- **Interactive Docs (Swagger):** http://localhost:8000/docs
- **OpenAPI JSON:** http://localhost:8000/openapi.json

### Ops Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Shallow heartbeat - always 200 if process is alive |
| GET | /api/v1/health/ | Deep check - pings SQLite + Upstash, returns 503 if DB unreachable |

Deep health response:
```json
{
  "status": "ok",
  "uptime_s": 3421.7,
  "components": {
    "database": { "status": "ok", "latency_ms": 1.2, "detail": "journal_mode=wal" },
    "cache":    { "status": "ok", "latency_ms": 43.1 }
  }
}
```

### Query Pipeline

| Method | Path | Description |
|---|---|---|
| POST | /api/v1/query/ | Main pipeline - full 8-stage processing |

Request body:
```json
{
  "query": "Write a Python function to parse JSON",
  "input_type": "text",
  "search_engine": "auto"
}
```

Optional header: Cache-Control: no-cache

Response:
```json
{
  "query_id": 42,
  "answer": "Here is a Python function...",
  "routing": {
    "rule_matched": false,
    "intent_category": "coding",
    "intent_score": 0.91,
    "requires_search": false,
    "requires_llm": true,
    "cache_hit": false,
    "engine_used": null,
    "model_used": "groq/qwen-2.5-coder-32b",
    "elapsed_ms": 1823.4
  },
  "sources": []
}
```

### Multi-Modal Ingestion

| Method | Path | Accepted Formats | Model |
|---|---|---|---|
| POST | /api/v1/ingest/audio/ | .wav, .mp3, .ogg, .flac, .m4a | OpenAI Whisper |
| POST | /api/v1/ingest/image/ | .jpg, .jpeg, .png, .gif, .webp | GPT-4o Vision |
| POST | /api/v1/ingest/video/ | .mp4, .avi, .mov, .mkv, .webm | ffmpeg + GPT-4o Vision |

All ingest endpoints accept multipart/form-data with a "file" field and return the extracted
text or description. The result can then be passed to /api/v1/query/ with the appropriate input_type.

---

## 10. Configuration and Environment Variables

All configuration lives in app/core/config.py via Pydantic v2 BaseSettings. Values are read
from the .env file or OS environment variables automatically.

### Complete .env Reference

```env
# Application
APP_NAME="AI Gateway"
DEBUG=false
HOST=0.0.0.0
PORT=8000
WORKERS=1

# Database
DATABASE_URL=sqlite+aiosqlite:///./gateway.db

# AI Models
EMBEDDING_MODEL=text-embedding-3-small
DEFAULT_LLM_MODEL=gpt-4o-mini

# LLM Provider Keys
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
COHERE_API_KEY=...
OPENROUTER_API_KEY=sk-or-...

# Search Engine Keys
TAVILY_API_KEY=tvly-...
BRAVE_API_KEY=BSA...

# Upstash Redis (Serverless Cache)
UPSTASH_REDIS_REST_URL=https://your-instance.upstash.io
UPSTASH_REDIS_REST_TOKEN=your-token

# Observability
SENTRY_DSN=https://...@sentry.io/...

# CORS
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080

# Litestream (SQLite to S3 backup, Docker only)
LITESTREAM_ACCESS_KEY_ID=...
LITESTREAM_SECRET_ACCESS_KEY=...
LITESTREAM_S3_BUCKET=my-gateway-backups
```

---

## 11. Observability and Logging

### Structured JSON Logs

Every log line is emitted as a JSON object written to logs/gateway.jsonl.
File rotation: 10MB per file, 5 backup files kept.

```json
{
  "ts": "2026-08-13T12:00:00",
  "level": "INFO",
  "logger": "app.services.dispatcher",
  "msg": "Query processed",
  "request_id": "3f2a-uuid-here",
  "input_type": "text",
  "cache_hit": false,
  "elapsed_ms": 1823.4
}
```

Key log fields:
- request_id: UUID4 per HTTP request injected by RequestIDMiddleware
- cache_hit: Upstash Redis hit or miss (true/false)
- intent: Classified intent category from semantic router
- engine: Which search engine (tavily/brave/duckduckgo) responded
- model: Exact model identifier used for LLM call
- elapsed_ms: Total wall time for entire pipeline in milliseconds

### Log Archiving

```bash
# Preview without making changes
python scripts/archive_logs.py --days 30 --dry-run

# Archive logs older than 30 days
python scripts/archive_logs.py --days 30

# Add to crontab (daily at 02:00)
0 2 * * * /app/.venv/bin/python /app/scripts/archive_logs.py --days 30
```

Archived files are gzip-compressed and saved to logs/archive/*.jsonl.gz

### Sentry Error Tracking

Activated automatically when SENTRY_DSN is set in .env. Captures:
- Unhandled exceptions with full stack traces and local variable values
- Slow transactions (10% sampling rate, configurable)
- LLM provider errors and timeout events
- SQLAlchemy query failures

Integrations enabled: FastApiIntegration (one transaction per endpoint) + SqlalchemyIntegration

### Deep Health Check

GET /api/v1/health/ runs two checks:
1. SQLite: executes PRAGMA journal_mode, measures round-trip latency
2. Upstash: writes and reads back a __health__ ping key with 5-second TTL

Returns HTTP 503 only if the database is down (critical dependency).
Returns HTTP 200 with status=degraded if Upstash is unreachable (cache is non-critical).

---

## 12. Deployment Architecture

### Docker Multi-Stage Build

Stage 1 (builder): python:3.12-slim + uv - installs all production dependencies
Stage 2 (runtime): python:3.12-slim + system ffmpeg + litestream binary

Key design decisions:
- SQLite database lives on a named Docker volume at /app/data/
- Litestream streams WAL changes to S3 continuously (when LITESTREAM_* vars are set)
- Single Uvicorn worker (WORKERS=1) required for SQLite single-writer constraint
- entrypoint.sh detects LITESTREAM env vars at runtime: if present runs litestream replicate
  as the process parent wrapping uvicorn; if absent starts uvicorn directly

### Litestream SQLite Replication

Litestream runs as the parent process inside the container:

```bash
litestream replicate /app/data/gateway.db s3://bucket/gateway.db \
  -- uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

Benefits:
- Continuous WAL streaming - changes replicated to S3 in near real-time
- Point-in-time recovery - restore database to any second from S3
- Zero performance impact - replicates WAL file, not query execution

### Cloud Platform Recommendations

SQLite requires a persistent filesystem. Use platforms with Persistent Volumes:

| Platform | Method | Notes |
|---|---|---|
| Render | Web Service + Persistent Disk | Set deploy hook URL for deploy.yml |
| DigitalOcean App Platform | App + Volume | Attach volume to /app/data |
| AWS EC2 or ECS | EBS volume | Mount at /app/data, push image to ECR |
| Fly.io | fly volumes create | Configure fly.toml volume mount |

### GitHub Secrets for deploy.yml

| Secret | Description |
|---|---|
| REGISTRY | Container registry URL (docker.io, ghcr.io, or ECR endpoint) |
| REGISTRY_USERNAME | Registry login username |
| REGISTRY_PASSWORD | Registry password or access token |
| IMAGE_NAME | Docker image name (e.g. yourname/ai-gateway) |
| DEPLOY_WEBHOOK_URL | Rolling restart webhook from your cloud provider |

---

## 13. CICD Pipeline

### CI Workflow - .github/workflows/ci.yml

Triggers: every push or pull_request to main or master branch

Steps:
1. actions/checkout@v4 - checkout repository
2. astral-sh/setup-uv@v5 - install uv with GitHub Actions cache
3. uv python install 3.12 - install Python
4. uv sync --all-groups - install prod + dev dependencies
5. ruff check app/ tests/ - lint with rules E, F, W, I, UP, B, C4, PLC
6. ruff format --check app/ tests/ - formatting verification
7. pytest tests/ -v - run all 46 tests with dummy API keys, zero live network calls

### Deploy Workflow - .github/workflows/deploy.yml

Triggers: only after CI workflow completes successfully on main branch

Steps:
1. docker/setup-buildx-action@v3 - set up Docker BuildKit
2. docker/login-action@v3 - authenticate to registry via REGISTRY secret
3. docker/metadata-action@v5 - generate tags: git SHA + latest
4. docker/build-push-action@v6 - build and push with GitHub Actions layer cache
5. curl DEPLOY_WEBHOOK_URL - trigger rolling restart on cloud provider

---

## 14. Running the App

### Prerequisites

```bash
# Install uv package manager
pip install uv

# Install all project dependencies
uv sync --all-groups

# Set up environment variables
cp .env.example .env
# Edit .env and add your API keys
```

### Local Development

```bash
uv run uvicorn main:app --reload
# Server running at: http://localhost:8000
# Swagger UI at:     http://localhost:8000/docs
```

### Docker Compose

```bash
docker compose up --build
# SQLite persists to the gateway_data named volume
# Logs bind-mounted to ./logs/ on your host
```

### Database Migrations

```bash
# Generate a new migration after changing ORM models
uv run alembic revision --autogenerate -m "add new field"

# Apply all pending migrations
uv run alembic upgrade head
```

---

## 15. Testing

```bash
# Run all 46 tests
uv run pytest

# Verbose output with short tracebacks
uv run pytest -v --tb=short

# Run tests for a specific phase only
uv run pytest tests/test_phase4.py -v

# Ruff lint check
uv run ruff check app/ tests/

# Ruff format check
uv run ruff format --check app/ tests/
```

### Test Coverage by Phase

| File | Count | Coverage Area |
|---|---|---|
| test_db_init.py | 1 | SQLite init + WAL mode verification |
| test_ingest_routes.py | 5 | Audio/image route registration + format validation |
| test_phase3.py | 14 | Rule engine regex, cosine math, Pydantic schemas |
| test_phase4.py | 13 | zlib compression, routing matrix completeness, TTL selection |
| test_phase5.py | 13 | Health endpoints, log archiver, Sentry gate, file existence |
| **Total** | **46** | **All phases - zero live API calls** |

All tests use dummy API keys ("test-key") and run entirely offline. Zero real network calls
or LLM costs are incurred when running the test suite.

---

## 16. Model Upgrade Policy

See MODELS.md for the full Standard Operating Procedure. Summary:

1. **Monthly review**: Check LMSYS Chatbot Arena leaderboard and HuggingFace Open LLM Leaderboard
2. If a new 30B+ open model outperforms the current primary in its intent category:
   - Update the model string in app/services/model_router.py in the ROUTING_MATRIX dict
   - Verify the new model ID is available and active on Groq / OpenRouter / Cohere
   - Run uv run pytest tests/ and confirm all 46 tests still pass
   - Commit with message: chore(models): upgrade coding to <new-model-name>
3. Keep the old model as position-1 in the fallbacks list for one sprint before removing it
4. Rotate all API keys (Groq, OpenAI, Cohere, OpenRouter, Upstash) every 90 days via cloud dashboard

---

*Last updated: 2026-08-13 | Built across 5 development phases | 46 automated tests passing*
