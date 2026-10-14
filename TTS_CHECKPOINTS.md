# Indexed TTS jobs and selective retry

Implemented on 2026-10-13. Public facade: `app.services.tts_service`. Sample generation keeps the same shared providers; background jobs now checkpoint each chunk.

## Request and worker flow

1. App key/origin, FastAPI owner grant and method/route permission are verified by the existing gateway dependency.
2. `POST /api/v1/audio/tts` validates the active model and persists the app ID, generation settings/model snapshot, lease token and indexed text chunks in one transaction. Existing HTTP 200 response is preserved.
3. Oversized paragraphs use NLTK English Punkt plus Unicode punctuation splitting: Gemini 2,500 characters, Kokoro 400. Missing tokenizer data uses the existing regex fallback and logs a warning. Indexes start at zero. Top-level `speed` survives background processing and retry.
4. The worker uses short database sessions. Each incomplete chunk gets an attempt count, runs through the shared generator, validates mono 16-bit WAV output, uploads to private B2, then commits its checkpoint. Provider credentials are resolved at execution time and are never returned in status responses.
5. Requests run concurrently under process-wide bounds: Gemini 2, Kokoro 1 by default. Gemini request starts, including retries, are spaced 0.5 seconds apart by default. Three transient retries allow four total attempts; quota-key rotation remains active. TTS cycles eligible DB key IDs, including 429 rotation. Same-key concurrency defaults to 2 (`TTS_GEMINI_KEY_CONCURRENCY=1` restores strict serialization); DB-empty fallback cycles distinct environment-pool keys. Other providers keep priority-first key selection. This distributes work without creating additional provider/project quota. Caller credentials retain precedence. Provider response and full chunk elapsed times are logged without secrets or text.
6. Every chunk must be complete before merge. Private checkpoint audio is fetched in index order, merged with a 200 ms gap for Kokoro, uploaded, presigned, and the completed job is committed. Only then are referenced temporary chunks deleted. Cleanup failures produce a warning and leave the completed result usable.
7. Existing completed/failed webhook events remain compatible; an additive `authorized_app_id` field identifies the gateway app. Webhooks still use the existing configured destination/HMAC secret.

## Poll and retry contract

`GET /api/v1/audio/tts/status/{job_id}` preserves the existing fields and adds:

```json
{
  "error": null,
  "retry_available": true,
  "chunks": {
    "total": 3,
    "completed": 2,
    "failed": 1,
    "items": [
      {"index": 0, "status": "completed", "attempts": 1, "error": null},
      {"index": 1, "status": "failed", "attempts": 1, "error": "GEMINI_QUOTA_EXCEEDED: provider quota exhausted; check quota before retry."},
      {"index": 2, "status": "completed", "attempts": 1, "error": null}
    ]
  }
}
```

`POST /api/v1/audio/tts/retry/{job_id}` needs the same app credentials and its own explicit route grant. No replacement text/model/key is accepted. HTTP 202 schedules the persisted job; HTTP 409 means completed, still running, legacy settings missing, or another retry already claimed it. HTTP 404 hides absent/foreign/unassigned jobs. All successful chunks are reused; failed, pending or abandoned processing chunks run again. Merge/upload failure retries reuse every valid checkpoint. A missing/corrupt stored checkpoint is marked failed when read; the next retry regenerates that chunk.

`attempts` counts checkpoint generation runs, not the provider's inner HTTP attempts. A quota failure should be retried only after quota/service capacity is available. Frontend progress displays completed/total and offers retry when `retry_available` is true. Call retry from the frontend server with its server-only gateway credentials and existing frontend per-user ownership checks. Never treat gateway app ownership as frontend user authentication. Studio, refresh, Library and content-detail recovery reuse the same owned status/retry paths and original job/settings without another credit deduction.

Status, retry and URL renewal now require the job's registered app ID. Renewal by URL must resolve a completed record belonging to that app; arbitrary object keys are rejected. Existing jobs stay unassigned: no owner is guessed or backfilled. Their data remains intact but the public app endpoints cannot expose them. Any verified legacy reassignment needs an explicit separately reviewed operation.

## Worker recovery and limits

- Default renewable lease: 1,200 seconds, heartbeat every 60 seconds. Retry can recover a processing job after its lease expires. An atomic conditional update selects one retry worker; token-conditional writes prevent superseded workers committing checkpoints/final results.
- Initial jobs use FastAPI BackgroundTasks; the lifespan maintenance service automatically claims expired processing jobs from existing DB rows every 30 seconds. It checks approved/active app, active API-enabled gateway owner, creation grant and generation snapshot. Healthy leases, failed/quota-limited jobs and legacy unassigned jobs are excluded. At most two recovery workers run at once. Restart recovery waits for the old lease to expire (default up to 20 minutes); manual retry uses the same atomic claim and fence. No external queue or extra migration is required.
- Concurrency and request pacing apply to one process. Multiple Uvicorn workers multiply capacity; keep `WORKERS=1` until shared provider/project rate limiting is implemented. Key usage counters do not reserve capacity atomically across processes.
- Final merging still holds WAV parts/result in memory. A 50,000-character/20-chunk functional load test with fake one-second WAVs verifies ordered output and concurrency bounds; it is not a real-provider memory/latency benchmark. Disk merge and distributed scaling remain future work.
- Referenced chunks are deleted after success. Hourly maintenance also sweeps only old unreferenced `.wav` objects beneath `tts_audio/chunks/`. It preserves every noncompleted job's referenced checkpoint and active worker prefix, and ignores recent objects and final audio. Default age is 24 hours, never less than twice the lease. Each sweep is bounded to 30 seconds; failures are logged by safe error category and retried next cycle.
- Chunk errors/logs retain safe categories, job ID, index and provider, not raw provider bodies, text, secrets or signed URLs. JSONL archival redacts credential fields and URL query strings before upload. Selected voice settings are never removed on HTTP 400; synthesis fails visibly instead of silently selecting another voice. Generative prosody can still vary between chunks even with the same voice.

## Settings and rollout

Environment overrides:

| Setting | Default | Purpose |
|---|---:|---|
| `TTS_GEMINI_CONCURRENCY` | 2 | Maximum active Gemini chunks per process |
| `TTS_KOKORO_CONCURRENCY` | 1 | Maximum active Kokoro chunks per process |
| `TTS_GEMINI_REQUEST_INTERVAL` | 0.5 | Minimum seconds between provider request starts |
| `TTS_LEASE_SECONDS` | 1200 | Worker lease lifetime; minimum 180 seconds |
| `TTS_RECOVERY_INTERVAL` | 30 | Seconds between expired-job scans |
| `TTS_ORPHAN_AGE_SECONDS` | 86400 | Minimum age before an unreferenced temporary WAV can be deleted; minimum 14400 |

Migration `e42b8d901c63` follows existing dashboard head `c93d5e71ab20`. It adds nullable job ownership/settings/lease fields and a unique `(job_id, chunk_index)` checkpoint table; it does not modify legacy ownership or text. Downgrade drops checkpoint data, so it is only for deliberate rollback.

```powershell
.venv\Scripts\python.exe -m scripts.migrate_tts_checkpoints --owner-email bilal.azeemlab@gmail.com --check
.venv\Scripts\python.exe -m scripts.migrate_tts_checkpoints --owner-email bilal.azeemlab@gmail.com
```

The current script requires checkpoint revision `e42b8d901c63` or live-preview head `f53c9e012d74`. It adds nullable `tts_chunks.frame_count`, preserves existing rows/grants, appends `GET /api/v1/audio/tts/stream/{job_id}`, and verifies schema and both origin owners. It never creates/replaces app keys or reassigns legacy jobs. Bootstrap includes both retry and stream grants.

Restart/redeploy the gateway after migration. Selected regression tests use isolated SQLite and fake provider/storage/webhook services. Real provider synthesis, production B2 writes/deletes, hosted deployment and frontend UI are not verified by these tests.

## Ordered live preview

`GET /api/v1/audio/tts/stream/{job_id}?after=0` requires the existing app key/origin and its own route grant. NDJSON events are `chunk` (`index`, `total`, base64 WAV `audio`), `completed`, `failed`, or `error`. The cursor is the next required zero-based index. If index 1 is missing while index 2 is ready, index 2 waits; retries resume at the browser's next unreceived index. Short DB sessions are released between polls.

Once the final audio is stored and completion committed, the existing worker deletes temporary B2 chunk audio. Frame counts allow late readers and cleanup races to reconstruct individual previews from the final WAV, excluding Kokoro merge gaps. Pre-migration completed jobs lack those counts and retain final playback only. Stream reconstruction reads the final WAV into memory; base64 adds about one-third to transfer size. This is chunk preview, not arbitrary byte-range or sample-by-sample PCM streaming.

The Next.js proxy validates owned history or exact cookie-bound guest proof before forwarding. Studio/landing reuse existing progress, retry and completion logic with `LiveAudioPreview`; users click **Listen live** to start playback. Blob URLs are released after playback/unmount. Proxy maximum duration is 300 seconds; unexpected EOF/network failures reconnect from the next received index with up to three consecutive failures (1/2/4 second waits), resetting the failure count when a chunk arrives. Explicit failed/completed events end the connection; ownership denials stop reconnecting. Final polling/download stays available.

Pending job IDs, original parameters and optional cookie-bound guest proof persist in owner-namespaced browser storage. On refresh the studio restores the job and checks progress once, without creating another generation or automatically retrying a failed/quota-limited job. Authenticated users can also restore from their server-owned history when browser storage is empty. Audio Library adds unfinished jobs plus the latest 50 server-owned pending history rows; **Resume in Studio** selects a specific job. Shared `AudioPlayer` shows progress/retry for missing audio on content-detail views. Server status/retry checks still determine ownership; browser storage never grants access. Pending records clear after successful download or inaccessible status. Browser-storage failures keep current generation usable; offline recovery across browser/profile changes is not available. Disable deployed response buffering; local fixtures do not verify hosted buffering.

## JSONL archival

The single-process root logger writes `logs/gateway.jsonl`. At 1,000 records it closes/renames the batch before upload. Example: `logs_20261013T090000000000Z__20261013T091500000000Z_abcdef123456.jsonl`. The timestamps are first and last record UTC times; the hash gives retry-stable keys. B2 key: `logs/2026/10/<filename>`.

Ready files stay in `logs/pending/`; the lifespan task attempts upload every five seconds. Only confirmed uploads delete local batches. Upload failures generate a readable error-category warning and retry later with the same key. Clean shutdown and startup recovery seal partial batches; interrupted malformed lines become safe recovery warnings. Private B2 credentials/bucket settings are reused. Run one worker until a shared logger/limiter exists; the local pending directory needs persistent storage if container restarts replace its filesystem.

NLTK 3.10.3, `punkt_tab` and `punkt` were verified locally. Docker installs/copies tokenizer data; runtime requests never download it. For a uv deployment run `.venv\\Scripts\\python.exe -m nltk.downloader -d .venv/nltk_data punkt_tab punkt` during provisioning. Docker image execution was not tested locally.

Previous recovery-phase verification (2026-10-13): 115 isolated backend tests, 48 frontend ownership tests, targeted changed-flow lint/typecheck and production Next.js build passed. Backend tests include interrupted-worker prefix reuse, revoked/invalid/healthy job exclusion, key cycling/serialization, safe paginated orphan cleanup and a 50,000-character/20-chunk functional load. Desktop/mobile Edge fixtures confirmed Library-to-Studio resume, refresh, network reconnect, ordered playback, content-detail retry and server-owned history fallback after clearing browser storage, without a second generation request. Actual provider synthesis, B2 upload/delete and hosted deployment remain unverified. See [TTS_TESTING.md](TTS_TESTING.md).

2026-10-14 correction: same-key requests now overlap under configurable bounds; environment pool fallback is honored. Library combines owner-filtered Studio and template results without duplicate job IDs. Its pending panel checks status on load/focus/every 15 seconds, syncs completed server results, removes finished/inaccessible records and refreshes the table. Offline completions save under stable job IDs. Ordinary same-account focus no longer hides the workspace; explicit identity changes retain loading/reload isolation. Verification: 117 backend and 50 frontend isolated tests plus online/offline Edge fixtures passed. Read-only frontend database checks returned the screenshot account's 28 completed Studio voices and verified actual list/single-URL SQL. No new migration or live synthesis/storage test.

## Verification completed

84 targeted isolated tests passed across `test_tts_chunks.py`, `test_tts_unified.py`, `test_tts_audio.py`, `test_app_authorization.py` and `test_bootstrap_genzee_apps.py`. Changed modules passed Ruff/compilation and `git diff --check`. SQLite migration upgrade/downgrade preserved the legacy record. The configured PostgreSQL migration and schema were verified at `e42b8d901c63`, and exact-origin apps 1/2 received retry grants without new keys. Windows sandbox asyncio initialization is blocked, so only these audited isolated suites ran with approved execution outside it. Full-suite and live provider/B2 synthesis were not run.

Configured PostgreSQL migration `f53c9e012d74` and `frame_count` schema were verified. Existing local/production apps 1/2 received streaming grants without replacing keys or mapping frontend users. Gateway/frontend restart or hosted redeployment is still required. New preview component/hook/route lint passes; the two existing page callers retain three pre-existing lint errors (verified against Git HEAD).

Startup recovery claims log files by atomic rename before reading. If an existing Windows process locks the active file, it is left intact and the new process uses `gateway-<pid>.jsonl`; abandoned process/recovery files are recovered on a later startup. This protects development restart overlap; production still requires one writer process.
