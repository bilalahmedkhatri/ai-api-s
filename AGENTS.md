# AI Gateway - Complete System Reference

> **Project:** AIModels_SearchAPIS
> **Runtime:** Python 3.14+ - FastAPI - PostgreSQL / SQLite (WAL) - Upstash Redis
> **Package Manager:** uv
> **Latest validation (2026-10-18):** 333 isolated backend tests passed after dependency cleanup; retained-library imports and offline lockfile consistency passed. Older counts below are historical, not current totals.

## Multi-app blog status and recheck (2026-10-24)

- This blog rollout is separate from the request-history/telemetry phases below. Backend code belongs in `app/blog/`; the active Next.js editor is `D:/voice_generator_free/admin_dashboard`, not this repository's legacy Remix `admin` checkout. Reuse the existing dashboard session, Origin, page/component grants and server-only transport.
- Blog targets come from the independent server-owned catalog, never `authorized_apps`: azeemlab, ailettergen, bondsgame, sikanderart, e_sahulat and genzee_video. The first, second, third and sixth have configured databases; Sikander Art/E Sahulat remain unconfigured. Each target stores its own posts/categories/assets under the same versioned `blog_0001` schema. Never fall back to the central database or migrate on startup/request.
- Keep slugs and grounded canonical/OG/Twitter/BlogPosting metadata backend-owned. Store validated Tiptap JSON only, not HTML/Base64 copies. Resolve categories in the selected app DB. Snapshot the existing membership/user name, designation and safe avatar into the post; no separate author table or central-user FK in app DBs. Preserve optimistic versions, published slug/locale stability and separate app/create/update/publish grants. No post/registered-asset DELETE endpoint, including for admins.
- Admin backend uploads to public B2 `blogPost001` under `blogs/<app_key>/<UUID>.<extension>`; consumer apps need no upload endpoints. Dedicated `BLOG_B2_*` credentials and `BLOG_<APP>_DATABASE_URL` stay in the backend environment. Use stable public download URLs, not signed downloads; existing private audio/log storage remains separate. Preserve upload-token retries, verified asset references, bounded media validation and exact-key orphan safeguards.
- Phases 1-5 are implemented for the four configured apps. Prior Phase 6 local write/browser acceptance recorded 70 passing checks, but the subsequent fresh recheck found unresolved issues. Do not present that historical receipt as unconditional current acceptance or production/deployment proof.
- **Open high-priority defect:** ordinary BondsGame connections repeatedly raise SQLSTATE `42P05`, `DuplicatePreparedStatementError`, through its configured pooler. `app/blog/database.py` currently uses NullPool without unique prepared-statement names. A temporary read-only diagnostic using UUID-based names passed; application code/configuration was not changed. Address pooler compatibility and repeat checks through the actual connector before clearing this issue. One successful schema/connection retry does not establish reliability; do not hide it with automatic write retries.
- **Open editor gaps:** internal sidebar navigation lacks unsaved-edit protection; category suggestions and cover choices load only the first 100 rows; the DB editor lacks several prior toolbar controls (strike, inline code, ordered list, blockquote, code block, horizontal rule). Existing-media selection is an additional improvement; the current picker uploads only. Reconcile stale README current-status text separately. These findings are documented, not fixed.
- Fresh recheck: 50 isolated backend tests, 63 offline blog tests and 43 Next frontend tests passed, plus typecheck/build/focused ESLint/Ruff. All four schemas verified; all eight retained B2 objects matched size/MIME (the BondsGame asset used the temporary diagnostic connection). This does not clear ordinary-connector failures. Prior acceptance sessions 21-24 remain revoked.
- Data snapshot at recheck: AzeemLab post ID 1 is published/version 5; the other five labelled acceptance posts remain archived. Do not automatically rearchive, delete or republish these records based on the older report; inspect current state before any mutation. The recheck changed no app data, permissions or application code.
- Deployment and consumer rendering/sitemap/cache integration remain deferred by user scope. Legacy localStorage drafts remain untouched; an explicit import flow is not implemented. Real Google login/limited-member provisioning, load tests and every supported codec are not covered by this recheck. Do not run `app/blog/acceptance*.py --run-live` as regression tests: those operator tools can create data/sessions.
- Evidence: [recheck](admin/output/BLOG_PHASES_RECHECK.md), `admin/output/blog-b2-recheck.json`, `admin/output/blog-bondsgame-diagnostic.json`, and historical [local acceptance](app/blog/PHASE6_ACCEPTANCE.md). No secrets belong in these reports.

## Deferred log findings (2026-10-19)

- User requested documentation only; discuss these findings before implementing fixes. The supplied local logs are evidence of individual runs, not a complete provider/storage acceptance test.
- Direct-generation repair returned HTTP 502 with `output_truncated` at 12:28:10 after earlier successful Groq `groq/openai/gpt-oss-120b` calls. The incomplete-output guard rejected the repair result. Review output budget, prompt size and repair-stage requirements; do not label this a TTS failure or silently increase budgets/retries. No fix or live retest is recorded here.
- Query and TTS maintenance repeatedly logged `deferred error_type=gaierror` between 12:39 and 13:07. This indicates hostname resolution failure; the exact hostname is absent from the sanitized logs. Database DNS/connectivity is a first investigation target, not a confirmed root cause. Both loops already catch failures and retry on their interval. Later successful requests do not establish a permanent fix. Investigate connection/DNS diagnostics without recording credentials or full database URLs.
- Historical finding: `/requests` previously read only query/final records. Phase 2/3 now connect the existing query/TTS/media tables through shared admin history APIs and the Remix UI; no job duplication/backfill or legacy table removal. Missing metrics/attribution remain explicit, and incomplete TTS provider attempts remain future telemetry work, not evidence of synthesis failure.
- Phases 1–3 completed locally on 2026-10-19 and Phase 4 on 2026-10-20; Phase 5 supported-flow telemetry completed locally on 2026-10-20; Phase 6 acceptance remains pending. Shared SQL UNION/filter logic in `request_history.py` drives admin `/dashboard/requests`, `/requests/summary`, `/requests/{record_id}`. Remix `request-records.server.ts` now feeds the existing `/requests` UI; obsolete query-only helper was renamed after reference checks. Reuse DataTable/Field/Submit/loading/Notice/cards/badges/error boundary and preserve admin/session/no-store controls. URL filters survive pagination/detail links; UTC calendar dates convert on the server to half-open API dates. Literal ID/model/provider search is SQL-filtered and shared by list/summary, never a browser-page-only search. Detail failures preserve the list except 401/403; old query_id bookmarks map to composite IDs. Keep old backend query APIs intact. Do not infer frontend-user/app identity from backend owners, detailed TTS provider calls from counters, or missing cost/timing. Failed history retains observed usage; customer zero is policy-derived, not a reconciled ledger. Reads never write usage, call providers/storage, renew URLs or expose raw configuration/credentials. Preserve native IDs/statuses, legacy tables and Phase 1 telemetry gaps. Phase 3 checks: typecheck/build, 25 frontend tests and 14 isolated history tests passed; browser checks used fabricated data, not configured DB/provider acceptance. See README for the browser-injected hydration-warning limitation.

- Phase 4: `/requests/summary` includes bounded daily UTC cohorts and recorded metric/sample counts from the shared filtered SQL source. Preserve null metrics versus zero requests, measured-sample weighted totals and a shared PostgreSQL timezone expression in SELECT/GROUP BY. The Remix loader fetches list/summary with identical date boundaries, validates summary data using existing Zod and preserves the table on graph failures except auth denial; `view=table` skips aggregation. Generic `components/chart.tsx` is reused by requests/Overview. User requirement: ALWAYS render visible numeric labels on recorded points and bars, never tooltip-only; dense ranges scroll with all labels. Model/provider/type/status bars count parent records, not attempts. Keep scopes/units/coverage and failed-history billing separation. Checks: 31 frontend and 21 isolated history/Overview tests, typecheck/build and targeted lint/diff passed; configured DB/provider/deployment acceptance remains separate.

## Phase 5 telemetry rules (2026-10-20)

- Reuse existing query metadata and server-owned `_telemetry` inside TTS generation JSON/media extra-parameter JSON. No schema/table retirement or new billing policy. Media parameters live under `parameters`; raw request extras remain the source of provider payloads. Snapshots retain execution model/provider/type/app; catalog edits never rewrite history. Preserve legacy incomplete coverage.
- `generation_telemetry.py` commits an attempt start immediately before outbound I/O and a safe terminal outcome afterwards. Eligibility skips, polling/downloads and merge/storage are not extra generation attempts. Gemini internal retries have separate attempt IDs under a stable chunk operation. Replicate outputs are separate operations; wait for all started siblings before parent finalization. Never infer these events from chunk counters.
- Short row locks merge JSON attempts; TTS sink writes are fenced by current run token and processing status. Preserve prior failures on retry. Never record secret keys, payloads, result/poll URLs or exception messages. Incomplete/unfinished persisted attempts stay incomplete, not invented zero usage. Audio token costs must not use chat prices; unknown costs stay null. Known subtotal and customer billing remain separate; failed/cancelled/rejected customer tokens stay zero.
- TTS terminal timing is original-submission to latest terminal wall time, including retry waiting. Never use updated_at or summed parallel latency. Active retries have no finalized duration. Optional X-External-User-ID is an authenticated app's opaque server-provided reference, limited to 128 non-control characters; scope it with the verified app ID, never treat it as users/memberships identity or an access grant. No CORS expansion or automatic frontend identity mapping.
- Keep query token graphs scoped to latest final responses. Attempt detail now exposes safe operation IDs/timestamps and recorded provider usage. Legacy snapshots/attempts stay unknown; no history-read writes. Phase 6 must verify real providers/prices, PostgreSQL concurrent writes/load and deployed/browser behavior. Media hard-stop recovery, rejection-parent coverage, invoice reconciliation, indexed workflow/user fields and a lossless event store are separate design work. Two JSON writes per outbound invocation are deliberate; measure growth before a new event table.
- Validation: 297 isolated backend tests across focused/retained-path suites, 31 frontend tests, admin typecheck/build and targeted Ruff pass. No real generation, billing writes, grants or schema changes. Do not claim provider or PostgreSQL write/load acceptance from mocks/read-only smoke.

## Lightweight gateway status (2026-10-18)

- PostgreSQL history UNION: use SQL `NULL`, not one shared bound `None`, across differently typed missing fields. asyncpg otherwise infers a float bind for boolean fields and rejects list/summary. Keep the asyncpg compilation regression; SQLite execution alone does not catch this. Read-only configured PostgreSQL list/summary both returned 80 parents after the correction on 2026-10-20; 22 isolated history/analytics tests passed. No schema or data changes.

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

## Unified model endpoint transition (2026-10-18)

- Keep ai_models as canonical identity/provider/pricing and provider_api_keys as the shared provider pool. New model_endpoints stores model FK, type, adapter, full URL, optional LiteLLM base, settings, active flag and created/updated timestamps; never duplicate identity/pricing or raw credentials there. The key link remains the existing provider-based association, not a key-specific FK.
- This is an additive/manual-validation phase: DO NOT remove tts_models, media_models, voices, history, snapshots or their current FK links. New nullable legacy links preserve IDs. New registrations create compatibility shells; loaders overlay new configuration without dirtying source ORM rows. Explicitly disabled linked configurations fail closed. Later table retirement requires the user's manual acceptance and a separate rewire/migration.
- Migration e610a2c947bf was applied/verified on configured PostgreSQL on 2026-10-18: 4 TTS + 5 media endpoints, 9 canonical imports, timestamps, unchanged old model rows/voice mappings/key identities/history and existing catalog prices/status. No grants changed. Unknown chat/embedding capabilities and URLs were not invented; imported pricing needs review. Backend deployment/restart is separate.
- Reuse the existing admin session/Origin/audit/no-store controls for GET/POST/PUT dashboard/model-endpoints. No delete route in this phase. Reject inline secrets, invalid settings and identity/type reassignment. Keep URL base separate from complete operation URL for LiteLLM.
- Query/direct chat use the shared selector/base options. TTS/media retain existing handlers, local-inference restrictions and old-job snapshots. Media reserves one existing provider-key slot per generation submission and serializes only shared-session DB work, never network calls; polling reuses its key. Preserve explicit legacy environment fallback pending manual credential testing. Endpoint storage does not add unsupported provider handlers. Semantic embedding selection still uses EMBEDDING_MODEL.
- See README's manual acceptance checklist. Run isolated migration/URL/key/voice/history/authorization regressions; no real-provider generation as part of regression tests.

## App authorization and ownership rules

- Gateway owners belong exclusively to this service's `users` table. Never map a frontend user ID into `owner_user_id`.
- Every protected app endpoint must use `get_current_api_key`: active app-bound key, active/approved app, active owner with `api_access_enabled`, and matching method/route permission. Current service-key authentication does not require Origin/X-App-Origin; optional registered-origin browser CORS remains separate.
- Origin headers are claims, not proof of identity. The server-side app secret authenticates the app. Never expose keys in browser bundles, URLs, logs or documentation.
- Facebook connections and publishing are scoped by `authorized_app_id`. Reconnect legacy unassigned tokens; never assign them automatically or accept a caller-supplied backend owner.
- App authorization does not replace the frontend's own session and per-user ownership checks. TTS job creation/status/retry/URL renewal and extracted media are app-scoped. Legacy unassigned jobs/media fail closed. Backend user CRUD and rollout rules are in `docs/USER_ACCESS.md`; reuse the shared operation policy for dashboard execution and direct API keys. Do not restore master-key activation or infer missing grants.
- Use `scripts/bootstrap_genzee_apps.py` for repeatable GenZee setup. Validate the existing Google owner and registered route templates before changing permissions. Reuse app registrations and saved active keys; reject conflicting origin ownership.
- Authorization revision: `a71c942e106b`; live-preview head: `f53c9e012d74` after checkpoint revision `e42b8d901c63`. Run regressions with isolated databases and mocked providers, never against configured production data.
- Read [APP_ACCESS.md](APP_ACCESS.md) for provisioning, current registration metadata, revocation and deployment requirements.

## Prompt-only generation and shared records (2026-10-18)

- Use the separate `POST /api/v1/generate/` route for a complete Next.js-built
  prompt. Accept only `{"prompt": "..."}`; reject extra fields, blank text and
  invalid Unicode. Return `answer`, nullable `title`, `request_id`, and `finish_reason` (`stop` only
  for an explicit provider stop, otherwise `unknown`). Preserve prompt/answer
  Unicode, whitespace and line breaks. Do not route through query search,
  retrieval, tools, RAG instructions, caches or content-repair calls.
- For initial cover letters, request optional title metadata in the same provider
  call using the server-owned JSON transport envelope in `generation_output.py`.
  Keep the original user prompt unchanged. Always include top-level `title` as a
  string or null: 3–7 lexical words, at most 80 NFC-normalized Unicode code points,
  in the requested output language. Ground substantive words in exact supplied
  role/company values; reject contact data, applicant-name sources and invented
  words/entities. Unverifiable translations/metadata fall back to null. No extra
  provider call or new required request field is allowed for titles.
  Parse the envelope so answer/persisted output contains the letter only. Invalid
  title metadata must not reject a usable answer. A fully closed JSON answer can
  survive truncated optional trailing metadata with title null and public finish
  reason unknown; incomplete answer strings and plain truncated text still fail.
  Review/repair and other tasks may omit title metadata. Save validated title in
  existing FinalResponse metadata, without new tables/columns. Include the system
  envelope instructions in context-budget checks. Next.js owns conversation.title:
  apply a valid AI title only after the first successful generation, preserve manual
  renames and retain the existing prompt-derived fallback for null/invalid titles.
  Optional-title evidence (2026-10-18): 187 selected isolated tests passed, including
  37 title tests; mocked transport/validation/storage is separate from live language
  quality and frontend conversation integration. See `tests/test_generation_titles.py`.
- Reuse the server-controlled DB chat selector, provider adapters and atomic
  provider-key reservations. The client cannot choose models, URLs, credentials
  or system prompts. Cap actual provider calls at four, disable SDK/LiteLLM
  retries, and stop automatic fallback after ambiguous timeout/connection loss.
  Refusal, empty/malformed output and known truncation must remain explicit errors.
- **No separate generation-record table:** reuse `queries` (`Query`) and
  `final_responses` (`FinalResponse`) with `request_type: "generate"` in metadata.
  Commit the verified app/key-owned processing row before provider work; save
  full validated prompt, available answer/partial text, internal model, tokens,
  cost estimates, attempts, normalized finish/error status and duration. Do not
  create dummy search rows. Failed partial text must never become a successful
  generation response. Never store secret headers, raw provider errors or reasoning.
- Reuse existing query history APIs and the admin `/requests` page for both
  request types. Enforce verified `authorized_app_id` ownership in list/detail
  SQL: customer A must never read customer B's records. Authorized admins can
  read all records through the existing admin-only dashboard handlers; ordinary
  dashboard members cannot. Legacy/unverified rows stay unassigned. A shared
  server key identifies an app, so Next.js must separately enforce end-user
  session/ownership checks; frontend user IDs must not become gateway owner IDs.
- Exclude generation rows in the query DB-cache SQL before its ten-row limit.
  Keep query POST/search/cache behavior otherwise compatible. Generation reads
  and writes no query cache, even when prompts or request IDs repeat.
- Finalize failure, deadline and cancellation records consistently. Reuse shared
  body-omitting rejection audits, request-local provider telemetry and the existing
  30-second maintenance/accounting loop. Completed generation usage is pending
  until the atomic usage-upsert/recorded-marker transaction succeeds. Failed or
  cancelled requests never add successful usage; unknown provider cost stays null.
  Existing stale-row recovery marks old processing requests failed without
  repeating generation. These statistics do not deduct frontend credits.
- Defaults: 32,768 prompt code points, 524,288 body bytes, 2,048 output tokens,
  45-second processing deadline, 20 seconds per provider call, up to 10 seconds
  for shielded DB finalization, four concurrent generation handlers and
  30 requests/minute per client IP/process. Keep `GENERATION_*` settings and
  sufficient provider context/output checks; never silently truncate input.
  Process-local bounds are not distributed quotas. Request IDs are tracing only.
- Regression evidence (2026-10-17): **150 selected isolated backend tests passed**,
  including 41 generation and 16 shared-record tests; targeted lint/format/diff
  checks passed. Providers were mocked, not live writing-quality evaluations.
  This is a scoped historical result, separate from later dependency-cleanup
  validation totals. Do not claim production persistence or deployment from it.
- Remaining rollout: restart/deploy the backend, configure an eligible active
  model/provider key, verify the generation and history GET grants for each
  intended app, update the Next.js adapter/limits, and perform separately
  authorized live provider/history checks. This feature adds no table/column or
  migration; existing checkout schema requirements still apply. Never deploy or
  make paid live calls merely to update documentation.

See [GENERATION_API.md](GENERATION_API.md) for the full contract, errors, settings,
test command and deployment boundaries.

## Query reliability

- Reuse existing Query/FinalResponse metadata and model usage upserts; no extra table or migration for recovery. Maintenance runs on lifespan startup and every 30 seconds with bounded batches, rotates deferred accounting rows, and never retries interrupted generation. Recovered failed records cannot be billed or cached. Cache hits correctly use zero new provider calls.
- Admin-only /requests uses shared UI and server-only Remix loaders. Both dashboard query-requests APIs require require_admin independently; app history remains app-scoped and legacy/unverified rows unassigned. No secret headers or rejected bodies may be stored. Test isolated SQLite transactions, concurrent replay, deadlines, ownership, admin authorization and frontend loaders; distinguish these from production/provider verification.

## Provider-key allocation

- Query medium fixes: processing cancellation must finalize as cancelled (internal classification 499), never completed; use shielded cleanup. All error finalization updates request and final metadata consistently. History filters read canonical Query.meta status, including cancelled. The query maintenance loop marks processing rows older than 10 minutes failed without reissuing provider calls; enforce the 180-second processing deadline. Direct greeting/thanks responses and fresh explicitly UTC time use zero chat tokens; time bypasses both caches. Weather/pricing/news/person lookups require search-backed async LLM; absent search results return an uncached unavailable answer without a chat call. Exclude old placeholders and failed/cancelled records from DB caches. DB and Redis hits must preserve model/intent/engine and copy normalized search sources into the new request history. Failed/cancelled/cache requests never add user token usage.


- Query request history: reuse Query.authorized_app_id plus existing query/final/search tables and metadata. POST persists ownership and request ID/key ID before processing; cache hits and sanitized failures must remain auditable. GET /api/v1/query/requests and /requests/{query_id} require normal exact endpoint grants, enforce app ownership in SQL, paginate and use no-store. Never assign legacy NULL-owner queries. Cache keys and DB cache lookups must be app-scoped; cache hits cost zero new chat usage. Track chat/embedding/search provider calls using request-local telemetry; available failed usage is provider-only. Unknown costs stay null and make total_provider_cost_usd null; expose known subtotal and coverage separately. Optional account-specific search per-call settings must not be guessed. Never call estimates a full provider invoice. Daily usage labels use app:<id>. Unauthorized/validation POST query failures receive sanitized rejected audit rows, omit bodies, and retain NULL ownership unless the key was verified; middleware logs remain the database-outage fallback. Migration d920f41a672e was applied and verified on the configured database on 2026-10-16 after explicit approval; only app ID 3 gained the two read grants, unrelated grants and legacy NULL ownership were preserved. Deploy/restart the backend separately; the schema migration does not deploy routes. Regression tests must stay isolated.

- Query provider I/O: `call_llm` awaits LiteLLM `acompletion`; semantic `embed` awaits `aembedding`. The shared chat router selects active `ai_models` whose local LiteLLM metadata declares chat mode (unknown capabilities fail closed), using preferred/free/lowest-price/ID ordering for the first candidate and inserting the active DB Replicate-hosted `replicate/openai/gpt-4o-mini` immediately second when there are multiple candidates. Exclude OpenRouter from this query flow. For this explicitly selected hosted GPT, reuse known base GPT chat metadata; do not broaden unknown-model capability acceptance. Cap actual provider calls at four after atomic key eligibility/reservation; missing keys do not consume that call budget, and later candidates must remain reachable. Use the row's provider with existing get_next_available_key; never silently fall back to environment keys. Provider retries are disabled; selected DB models are tried explicitly. Query I/O reserves the provider slot through get_next_available_key(reserve=True) using a conditional atomic UPDATE. mark_key_used(reserved=True) updates last-use without incrementing twice; 429 reuses mark_key_quota_exceeded. Other callers keep existing defaults. Never release a failed attempt into user token accounting: provider-request quota and user token charges are separate. User record_model_usage runs only after successful request/final-response persistence, with atomic daily upserts. Bookkeeping failures must preserve the successful answer and durable model/tokens/cost; persist usage_accounting_status=pending when available. Pending reconciliation runs every 30 seconds; atomically commit the query compare-and-set recorded marker and usage upsert together, using the original UTC request date. Never split those commits or charge failed/cache/cancelled requests. No token charge for failed attempts or failed requests, regardless of provider usage. Lifetime allocation total_count remains claim-endpoint-only; the query reservation is a database-atomic daily slot, not a provider RPM or distributed concurrency limiter. Persist sanitized attempt statuses/types/timings and successful actual model/tokens in FinalResponse metadata. Failed generations return 503 and cannot be reused from DB/Redis caches; no-cache bypasses both layers. Never persist exception text or credentials. The separate synchronous vision helper is not part of this query flow.

- Deferred customer-key work (do not implement until requested): (1) replace automatic grants of all available app endpoints with plan-based endpoint permissions and per-user usage quotas; (2) enforce an active-key count limit. Existing issuance rate limiting is not a usage quota or active-key cap. Keep current generation, ownership and revocation intact while these controls remain deferred.

- Reuse the admin-protected `POST /api/v1/keys/provider/{provider}/claim` and `key_manager.claim_provider_keys` for explicit server-to-server key allocations. Return only raw key strings with no-store; never log secrets or accept ordinary app/customer keys as admin authority.
- Select at most two active, under-quota provider rows, ordered by lifetime total_count, requests_today, priority, newest ID. Selection/increments are a single UPDATE; PostgreSQL locks and skips busy rows. Decrypt before commit and roll back corrupt credentials. Daily resets must not clear total_count.
- Counts represent reservations, not confirmed provider requests. Do not double-count a claim through mark_key_used, and do not silently wire existing TTS into claims. Migration c84f10d2a639 follows ab92d18e740c. Test only with isolated databases/fake credentials; separate PostgreSQL live concurrency from SQLite/SQL compilation evidence.

## Unified TTS implementation (2026-10-13)

- Remote Kokoro uses the existing HTTP adapter. Local ONNX inference is removed from this gateway: `is_local` Kokoro is unavailable and `allow_local_fallback=true` cannot restore it. Do not enable local fallback here. Worker transport, submission retries and a generic worker adapter are not implemented.

- Use `app.services.tts_service` as the canonical public facade for samples and background jobs. It re-exports modular implementations and must never contain a second provider or job implementation.
- `app.services.tts.tts_service` remains only as a compatibility import path. Provider changes belong in `app/services/tts/`.
- Gemini's shared policy is 3 retries / 4 attempts per chunk, capped backoff with jitter, transient 5xx/network retries, DB key rotation on 429 and header-based provider credentials. Chunk limits are Gemini 2,500 / Kokoro 400 characters. Never strip the requested voice configuration on HTTP 400.
- Long scripts use request-schema validation; do not reintroduce a smaller hardcoded generator-only limit.
- Background jobs use `tts/jobs.py` for result metadata, persisted failure reason and configured webhook delivery. Test both actual API paths with isolated SQL and fake providers/storage/webhooks.
- Completed TTS status exposes stored duration_seconds and size_bytes along with the final URL; frontend Library completion sync depends on both fields. Preserve app-scoped authorization on metadata access.
- Indexed checkpoints and selective retry are now implemented in `tts/jobs.py`; see [TTS_CHECKPOINTS.md](TTS_CHECKPOINTS.md). Creation saves app ownership, model/settings snapshots and chunks atomically. Never infer legacy ownership.
- Use fenced worker tokens and renewable leases for every checkpoint/final write. Retry must be an atomic claim, must reuse successful checkpoints, and must reject foreign/unassigned jobs. Only delete chunk audio after the final completed result commits.
- Default process-wide chunk concurrency is Gemini 2/Kokoro 1, with 0.5-second Gemini request-start pacing derived from constants (environment override wins). Gemini cycles eligible DB keys under a process lock; same-key requests use `TTS_GEMINI_KEY_CONCURRENCY` (default 2; set 1 for strict serialization). DB-empty fallback cycles distinct `GEMINI_API_KEYS`, falling back to `GEMINI_API_KEY`. Other key-manager callers keep priority-first behavior. These are not distributed provider/project rate limits; keep one worker until shared limiting exists. Log provider response and complete chunk elapsed times without credentials or input text.
- Migration `f53c9e012d74` adds nullable checkpoint frame counts. The existing migration script validates parent/owner and appends the streaming grant without issuing keys. `tts/streaming.py` emits only the next contiguous chunk; after cleanup it reconstructs previews from the final WAV using frame counts. Older completed jobs without frame metadata still have their final audio, but cannot reconstruct previews.
- NLTK English Punkt plus Unicode punctuation handles oversized paragraphs. Install `punkt_tab` and `punkt` before deployment; Docker includes them. No tokenizer downloads in requests. Missing data falls back to the existing splitter with a warning.
- Reuse the root JSONL logger: seal at 1,000 entries, rename with UTC first/last record timestamps plus content hash, and upload to private B2 `logs/YYYY/MM/`. Retain failed uploads locally. Keep one writer process; partial batches seal on clean shutdown/recovery.
- Prefer the smallest tested change using existing helpers; re-check references before deleting code. `tts/maintenance.py` uses existing rows/leases and the shared atomic retry claim. Recover only expired processing jobs with approved apps, active API-enabled owners and creation permission; never auto-retry quota/failed jobs or guess legacy ownership.
- Maintenance scans every 30 seconds and schedules at most two recovered jobs. Hourly cleanup only considers old temporary WAVs, preserving incomplete-job checkpoints and active run prefixes. Default age is 24 hours and at least twice the lease. Never sweep final audio. Production logs need persistent storage; compose already mounts `./logs:/app/logs` and uses one worker. Docker context excludes local secrets, environments, logs and databases.
- Shared distributed limits, sample-by-sample delivery and memory-bounded final merge remain future scaling work; real-provider/B2 testing is separate from mocked regressions. See [TTS_TESTING.md](TTS_TESTING.md).
- Prior unification verification on 2026-10-13: 60 tests across `test_tts_unified.py`, `test_tts_audio.py` and `test_app_authorization.py` passed, plus changed-module Ruff and compilation. Full-suite execution in the Windows sandbox stalled at asyncio's internal socket initialization; only these audited, isolated suites were run outside it with approved execution.

- Checkpoint verification (2026-10-13): 84 targeted isolated regressions passed, including ordered merge, selective retry, foreign/legacy job denial, overlapping retry, worker fencing, heartbeat loss, invalid/missing audio, presign/upload/cleanup failure and SQLite migration upgrade/downgrade. Configured PostgreSQL migration/schema and both exact-origin retry grants were verified. Full suite and live synthesis/storage/deployment were not run.

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
| Multi-modal input ingestion | Image (GPT-4o Vision), Video (ffmpeg + Vision); Whisper moved to worker sources |
| Multi-modal audio synthesis (TTS) | DB-configured Gemini TTS + remote Kokoro; local Kokoro unavailable |
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
|   Whisper transcription --> relocated worker source (not served) |
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
|   |   |-- tts_service.py          # Backward-compatible TTS facade
|   |   |-- tts/                    # Modular TTS Subsystem
|   |       |-- constants.py        # Timeouts, retries, character limits, sample dirs
|   |       |-- text_utils.py       # Multi-language text chunking (Latin, Hindi, Urdu, Chinese)
|   |       |-- audio_utils.py      # PCM/WAV conversions, WAV merge, duration & size extraction
|   |       |-- sample_cache.py     # Voice sample local cache & path traversal guard
|   |       |-- catalog.py          # Dynamic models & voices DB queries
|   |       |-- gemini_provider.py  # Gemini TTS provider with retry & key rotation
|   |       |-- kokoro_provider.py  # Remote Kokoro HTTP; local calls reject
|   |       |-- generator.py        # Central dynamic TTS speech dispatcher
|   |       |-- jobs.py             # BackgroundTasks job orchestrator (B2 upload + status)
|   |       |-- notifier.py         # HMAC-SHA256 signed webhook notification to frontend
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

| Input/Output Type | Model selection | Provider | Endpoint |
|---|---|---|---|
| TTS synthesis | Active DB model | Gemini / remote Kokoro | `/api/v1/audio/tts` |
| Voice list and samples | Active available DB model | Gemini / remote Kokoro | `/api/v1/audio/voices`, `/api/v1/audio/sample` |
| Image vision | Configured vision model | Existing vision adapter | `/api/v1/ingest/image/` |
| Video frames and vision | Configured vision model | FFmpeg + existing vision adapter | `/api/v1/ingest/video/` |

Whisper transcription and local Kokoro are not served. Legacy provider-specific
`/audio/gemini-tts`, `/audio/gemini-voices`, `/audio/gemini-sample` and `/audio/speech`
examples are not the current API contract. Use the shared routes above and OpenAPI.

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
| **Google AI Studio** | Gemini 3.6 Flash Audio Output & TTS | GEMINI_API_KEY |
| **OpenAI** | Configured vision and embedding requests; no local Whisper STT | OPENAI_API_KEY |
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

### Table: tts_jobs

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment primary key |
| job_id | VARCHAR(64) indexed | Unique UUID identifier for the background TTS job |
| title | VARCHAR(255) | Optional user title or first 100 characters of text |
| text | TEXT | Complete text to synthesize |
| status | VARCHAR(32) | Current status (`processing`, `completed`, `failed`) |
| audio_url | TEXT | Active pre-signed URL or direct link to generated audio |
| object_key | VARCHAR(512) | Backblaze B2 / S3 object key (e.g. `tts_audio/tts_...wav`) |
| duration_sec | FLOAT | Duration of generated audio in seconds |
| size_bytes | INTEGER | Size of generated WAV file in bytes |
| error_message | VARCHAR(500) | Detailed error message if the job fails |
| created_at | DATETIME(TZ) | Creation timestamp |
| updated_at | DATETIME(TZ) | Last update timestamp |

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
| POST | /api/v1/ingest/image/ | .jpg, .jpeg, .png, .gif, .webp | GPT-4o Vision |
| POST | /api/v1/ingest/video/ | .mp4, .avi, .mov, .mkv, .webm | ffmpeg + GPT-4o Vision |

Image/video ingestion accepts multipart/form-data. Whisper file/URL transcription is preserved in the separate worker source directory; neither audio ingestion route is registered in this gateway. Video keyframe analysis does not require Whisper.

### Multi-Modal Audio & Speech (TTS)

| Method | Path | Request Format | Description / Model |
|---|---|---|---|
| GET | /api/v1/audio/models | None | Lists available active TTS models; excludes local Kokoro |
| GET | /api/v1/audio/voices | Query: `?model=...&search=...&limit=10&offset=0` | Paginated & searchable voice list for requested model |
| GET | /api/v1/audio/sample | Query: `?model=...&voice=...` | Streams cached or on-demand WAV sample audio preview |
| POST | /api/v1/audio/tts | JSON `DynamicTTSRequest` (`model`, `text`, `voice`, `lang`, `title`, etc.) | Triggers background TTS job; returns `job_id` |
| GET | /api/v1/audio/tts/status/{job_id} | Path: `job_id` | Checks status, returns pre-signed audio URL & timestamps |
| POST | /api/v1/audio/tts/renew-url | JSON `{"job_id": "..."}` or `{"url": "..."}` | Renews expired Backblaze B2 7-day pre-signed URL |

### Async Media Extractor

| Method | Path | Description |
|---|---|---|
| POST | /api/v1/media/process-urls | Submits keywords for background image downloading from Pexels/Pixabay |

Request body:
```json
{
  "keywords": ["nature", "sports cars"],
  "filters": {
    "orientation": "landscape",
    "sort_by": "most_downloaded"
  }
}
```

Returns `202 Accepted` immediately. A FastAPI `BackgroundTask` will run in the background, fetch up to 3 high-quality images per provider (Pexels and Pixabay) for each keyword, and save them to the `static/download/` directory on the server. *(Note: The callback URL has been removed as requested, so the backend handles the images entirely internally).*
*Note for future updates: Consider Option A (uploading the downloaded images to a cloud storage like S3 and sending the frontend a list of generated URLs) if local storage becomes a bottleneck.*

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

# LLM & Multi-Modal Provider Keys
GEMINI_API_KEY=AIzaSy...
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

# Backblaze B2 (Audio and Media Storage)
B2_BUCKET_NAME=my-bucket
B2_APPLICATION_KEY_ID=...
B2_APPLICATION_KEY=...
B2_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com

# Frontend Webhook (TTS Background Job Notifications)
FRONTEND_WEBHOOK_URL=https://myfrontend.com/api/tts/webhook
FRONTEND_WEBHOOK_SECRET=your-webhook-secret
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

The current Dockerfile and CI configuration still target Python 3.12, while `pyproject.toml` and `.python-version` require 3.14. These legacy deployment paths need alignment before use; the 333-test validation used the local Python 3.14 environment and does not validate Docker/CI deployment.

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

# Install locked project dependencies (Python 3.14+)
uv sync --locked --all-groups

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
# Run the backend regression suite with locked dependencies
uv run --locked pytest tests/

# Verbose output with short tracebacks
uv run pytest -v --tb=short

# Run tests for a specific phase only
uv run pytest tests/test_phase4.py -v

# Ruff lint check
uv run ruff check app/ tests/

# Ruff format check
uv run ruff format --check app/ tests/
```

### Historical initial-phase test coverage

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
   - Run uv run --locked pytest tests/ and resolve regressions in the current suite
   - Commit with message: chore(models): upgrade coding to <new-model-name>
3. Keep the old model as position-1 in the fallbacks list for one sprint before removing it
4. Rotate all API keys (Groq, OpenAI, Cohere, OpenRouter, Upstash) every 90 days via cloud dashboard

---

*Last updated: 2026-08-13 | Built across 5 development phases | 46 automated tests passing*


## Dashboard backend ownership

The Remix admin now uses backend-owned Google OAuth, memberships, permissions, sessions, verified access_requests, email_challenges, service_credentials and audit_logs through /api/v1/dashboard. Dashboard membership IDs never identify gateway owners. Dashboard approval is independent of users.api_access_enabled. Existing app API dependencies remain unchanged. Migration head for this dashboard extension is c93d5e71ab20, following a71c942e106b. Bootstrap an explicitly approved administrator with python -m scripts.bootstrap_dashboard and DASHBOARD_BOOTSTRAP_EMAIL. Deployment configuration and legacy frontend SQLite retirement are documented in admin/README.md. Regression tests use isolated databases and mocked Google, SMTP and HTTP providers.
