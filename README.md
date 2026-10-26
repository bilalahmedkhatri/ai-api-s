# AI Gateway & Search APIs

## Deferred findings from local logs (2026-10-19)

These items are recorded for later discussion at the user's request. No runtime fix or live retest is implied.

- **Truncated repair output:** direct generation returned HTTP 502 / `output_truncated` at 12:28:10, after successful Groq `groq/openai/gpt-oss-120b` generation/review calls. The output guard rejected an incomplete repair response. Pending review: output budget, prompt size, repair-stage requirements and caller error handling. This is separate from the later successful Gemini TTS job; it does not prove a provider URL/key error.
- **Maintenance DNS failures:** both query and TTS maintenance logged `deferred error_type=gaierror` repeatedly between 12:39 and 13:07. The hostname is not present in the sanitized logs, so the exact failing service is unconfirmed. Check database hostname resolution/connectivity first and correlate safe diagnostics with connection attempts. Both maintenance loops already retry after their interval; later successful requests do not prove the underlying issue is permanently resolved. Do not copy credentials or full connection URLs into diagnostics.

### Unified AI request history and graphs: phased delivery

Scope: one admin `/requests` page for recorded query, direct text generation, TTS, image and video work, with model/provider filters, a paginated table, request detail and aggregate graphs. Embeddings/search are child operations of a parent query when recorded; do not count them as additional user requests. A standalone operation needs its own recorded lifecycle before it can appear as an independent request. Registering a model in `ai_models`/`model_endpoints` does not create generation history.

Current sources: query/generate use `queries` + `final_responses`; TTS uses `tts_jobs` + `tts_chunks`; image/video use `media_generations` with legacy model links. Existing Overview request metrics aggregate dashboard `AuditLog` HTTP calls, whereas its token/cost metrics use `final_responses`; these must not be presented as unified AI job counts. Existing `AnalyticsPanel` contains a small SVG `Chart`; `DataTable`, cards, badges and server-only loaders are reusable. No chart library is needed for initial trend graphs.

**Phase 1 — Complete: source inventory and metric contract (2026-10-19).** The documentation contract below is finalized following source inspection. Phases 2–5 provide read APIs, table/detail UI, numeric graphs and supported-flow telemetry; Phase 6 manual acceptance remains pending. Failed requests retain their attributable history and observed provider usage but contribute zero customer-chargeable tokens.

**Phase 2 — Complete: shared read layer and admin APIs.** `app/services/request_history.py` provides a common SQL UNION ALL projection across existing sources, latest query response selection, normalized statuses and shared filters for SQL pagination/counts/aggregates. Composite IDs distinguish query/TTS/media sources. Admin list/summary/detail routes reuse dashboard session/role checks and no-store responses; old query APIs and app-facing contracts remain unchanged. Query detail reuses existing serialization but only exposes selected metadata/attempt fields. TTS detail shows recorded chunks without inventing per-provider attempts. Media remains owner-linked with no guessed app mapping. No history table, migration, billing write, audio generation, signed-URL renewal or frontend change was introduced. Daily chart series are added in Phase 4; Phase 5 supported-flow telemetry and optional app references are described below. See the API contract and verification below.

**Phase 3 — Complete: mixed request table and detail UI.** `/requests` now uses server-only `admin/app/lib/request-records.server.ts` to read the shared admin APIs; sidebar/page labels say AI requests. Existing DataTable, Field, Submit/loading, Notice, cards, badges and error boundary are reused. Type/status/model/provider/app/backend-owner/UTC-date/literal-search/page-size filters live in the URL and survive pagination/detail navigation; applying filters resets pagination. Date inputs use UTC calendar dates with exclusive end. Search is a small shared backend filter over record ID/model/provider, not page-only filtering or prompt search. Detail displays selected query attempts, TTS voice/audio size/duration/chunk counters or media dimensions/duration/output count; large text is collapsible. Missing metrics stay unknown and zero customer tokens are explicitly policy-derived, not ledger verification. Detail 404/5xx preserves the list with a safe notice; auth failures still deny access. Legacy query_id bookmarks map to new composite detail IDs; the obsolete query-only helper was renamed after reference checks. No schema/billing/provider changes. Graphs are added in Phase 4 below.

**Phase 4 — Complete: numeric graphs from shared filters.** Summary now includes bounded daily SQL aggregates using the same filtered source as the table, zero-filled request counts, nullable unmeasured metrics, coverage counts and current-status creation-date cohorts. The Remix server resolves one exact date window for list/summary, fetches independently in parallel and validates graph data with existing Zod. `/requests` defaults to table and graphs; `Display: Table only` skips the summary request. A metric selector reuses one extracted generic `components/chart.tsx` for requests/completed/failed/success rate/timing/input tokens/output tokens/known cost; expandable bars show model/provider/type/status counts, and a daily table shows figures/sample counts. **Numeric values must be visible on every recorded point/bar, not tooltip-only.** Dense ranges scroll without dropping labels; missing values show N/A/Not recorded rather than zero. The shared chart also gives existing Overview graphs numeric labels while preserving their dashboard-traffic scope. Graph API/schema failures preserve the table with a safe retry notice; auth failures still deny access. No new chart library, provider calls, schema migration or billing changes.

**Phase 5 — Complete locally: supported-flow telemetry (2026-10-20).** Existing metadata/snapshots now store safe provider attempt IDs, operation correlation, immutable terminal timing where recorded, execution model/provider snapshots, observed usage/cost coverage and optional app-scoped external-user references. TTS retries and media fan-out retain earlier failures without counting polling as new generation. Legacy/unavailable measurements remain unknown; failed customer tokens remain zero. No migration, new history table or billing policy. The implementation and remaining manual acceptance boundaries are detailed below.

**Phase 6 — Verification and staged acceptance.** At each phase run focused isolated tests. Cover old/new records, mixed-source IDs, ordering/pagination/filter counts, status mappings, unauthorized/non-admin denial, unchanged app isolation, null/partial metrics, repeated retries, multi-chunk TTS and fan-out without double counting. Verify graph totals match filtered table totals across all pages, UTC boundaries, cache accounting and failed-request customer usage. Check bounded query plans on representative PostgreSQL data before adding indexes; no new cache/materialized table until measurements justify it. Run admin type/lint/build checks and manual desktop/mobile/keyboard checks with empty/loading/error/partial-data states. Then manually accept one small real request per configured supported type, including a failed case; mocked tests do not prove provider success. Keep legacy model tables/history/FKs and existing API contracts throughout. Phases 1–5 are implemented locally; Phase 6 live/manual acceptance remains pending.

### Phase 5 implementation and acceptance (2026-10-20)

- Reuse `query_telemetry.py` for observed chat/embedding/search usage. Query/chat attempts now include IDs, operation kind, provider where recorded, actual response model and UTC start/finish times. Fallbacks belong to the same logical operation; skipped keys are not outbound calls. Existing failed-request accounting guards/reconciliation are retained. Cached chat creates no fake provider attempt and does not erase newly executed embedding/search usage.
- Shared `generation_telemetry.py` records TTS/media invocation starts before outbound I/O and terminal outcomes after processing. Existing `tts_jobs.generation_json` and `media_generations.extra_params_json` contain server-owned `_telemetry` metadata; no new table/column/migration/dependency. Media parameters are serialized under `parameters` and are still forwarded from the validated request, never from telemetry. Query uses existing query/final metadata. Missing legacy fields are not guessed or backfilled.
- Gemini records each internal HTTP retry/key rotation separately under its stable `chunk:<index>` operation. Remote Kokoro records one generation submission, including its polling/download outcome; polling is not another generation. Chunk attempts/counters stay separate from provider-call events. Events retain safe IDs/times/model/provider/status/HTTP status and reported usage, never keys, request/response bodies, URLs or raw exception messages.
- Short row-locked JSON updates merge simultaneous attempts; TTS writes require the current worker token/status. Retry/resume retains earlier failed attempts. A hard stop can leave a durable processing attempt with unknown outcome, not a fabricated failure/cost. Legacy resumed jobs remain explicitly incomplete. New supported jobs carry versioned coverage; pending parents are not advertised as complete in detail.
- TTS terminal metadata records elapsed wall time from original submission to the latest terminal transition. This includes queueing and manual retry waiting; it is not summed parallel-provider time or audio playback duration. Renewal/heartbeat does not rewrite this measurement. Running retries are excluded from TTS elapsed averages until a new terminal boundary is recorded.
- Image/video persist app attribution and execution-time model/provider/type snapshots, and execute the same already-resolved model config. Later catalog edits cannot rename historical executions. Replicate fan-out records each actual output submission separately; all started siblings settle before the parent is finalized, so a failed parent retains successful siblings too. This can delay an error response until the slowest sibling finishes/times out. Cancellation keeps safe parent/attempt status where finalization can persist.
- Optional server-to-server `X-External-User-ID` (nonempty, at most 128 characters, no control characters) is an opaque reference asserted by the authenticated app. The same value in two apps is not the same identity. Stored app IDs come from existing authorization, never that header. It grants no access, performs no gateway user lookup, adds no cross-app user join and changes no browser CORS allowance. Omit it when the calling server has no trustworthy user reference. Existing clients need no payload change.
- Reported failed usage remains observable while failed/cancelled/rejected customer-chargeable tokens stay zero. Successful TTS/media customer tokens remain unreported; no new billing policy/ledger is invented. Gemini audio input/output token counts are saved where supplied, but chat token prices are not applied to audio. Provider-specific unavailable pricing stays null, with known subtotals and explicit incomplete cost coverage. Parent token graphs still cover latest query responses, not a sum of TTS/media attempts or a total provider bill.
- Admin detail reuses the existing attempts table with operation IDs/start times and an app-provided-user-reference label. SQL list/summary consume recorded TTS terminal timing and TTS/media known-cost coverage; media app/model/provider filters use snapshots when present, with catalog-derived legacy values clearly labeled. Graph/table parent counts remain one per submission.
- Validation: 297 isolated backend tests across the focused and retained-path suites, plus 31 frontend tests, admin typecheck/production build and targeted Ruff checks passed. Cases include internal retry, failed-chunk resume, consumed failed usage/zero customer tokens, cancellation, no-key/no-call, fan-out with a failed sibling, polling count, parameter serialization, execution snapshots, app-reference isolation, safe errors and unchanged auth/accounting. Configured PostgreSQL list/summary/detail read-only smoke passed with matching parent totals; this is separate from provider acceptance; no live generation or billing writes are part of these checks.

Remaining acceptance boundaries: real-provider pricing/usage accuracy, configured PostgreSQL concurrent-write/load behavior, browser/deployment acceptance and one real successful/failed request per configured type remain Phase 6. Unauthenticated TTS/media pre-handler rejections and media pre-record model-validation failures do not acquire invented parent rows. Media hard-stop automatic recovery, indexed external-user/workflow correlation, provider-invoice reconciliation and lossless event delivery require separately designed workflows/schema where necessary. Existing query/TTS maintenance remains unchanged. JSON attempt arrays add two short commits per invocation and can grow across retries; measure before designing a separate event store. Persistence failure blocks further outbound work or leaves incomplete coverage rather than claiming lossless success.

### Phase 1 contract: request, operation and attempt

**Historical Phase 1 source inventory; Phase 5 changes for new records are described above and below**

| Source | Parent record and lifecycle | Existing child/usage data | Limits for the shared history |
| --- | --- | --- | --- |
| Query | `Query`; status/duration/error/correlation in `Query.meta`; latest `FinalResponse` supplies answer/model/tokens | `model_attempts` in final metadata; observed `provider_calls` and cost coverage in query metadata; `search_results` | Attempts and provider calls can describe the same invocation: never sum both. `authorized_app_id` identifies the app, not its frontend user. |
| Direct generate | Same tables; `request_type=generate`; processing then completed/failed/cancelled | Generation trace attempts and observed provider calls; final usage where returned | Separate HTTP generate/review/repair requests are separate parents today; a shared client request ID is not proof of a common workflow. |
| TTS | `tts_jobs`: job ID, app ID, text, status/error, generation snapshot, created/updated timestamps, audio duration/size | `tts_chunks`: stable zero-based index, status, attempts and latest error | Gemini has internal retries/key rotation. Chunk `attempts` is not a complete provider-call count; per-attempt history/tokens/cost are not persisted here. `updated_at` is mutable, not an immutable finished timestamp. |
| Image/video | `media_generations`: native ID, legacy model ID, pending/success/failed/timeout, elapsed time, outputs/error | Output count and parameters; model lookup through retained legacy links | `user_id` comes from the backend app owner; no persisted app ID or frontend user ID here. No complete durable fan-out/attempt usage history or execution-time provider snapshot is present. Pre-record model validation errors are not covered by this table. |
| Pre-handler rejection | Middleware records minimal rejected `Query` rows for query/generate when possible | Safe HTTP status, request ID, duration; body omitted | No authenticated identity is invented. This does not establish equivalent rejection coverage for TTS/image/video. |

Files inspected: `app/api/v1/query/router.py`, `app/api/v1/generate.py`, `app/core/middleware.py`, `app/services/query_telemetry.py`, `app/services/query_maintenance.py`, `app/services/model_router.py`, `app/services/generation.py`, `app/api/v1/image/router.py`, `app/api/v1/video/router.py`, `app/services/tts/jobs.py`, `app/services/tts/gemini_provider.py`, and ORM models. This is source verification, not live-provider or database acceptance.

**Hierarchy and identity**

- Parent = one accepted generation submission/job, not one unique user. Keep a failed parent record even if it has no output. A retry of a chunk/model inside the same job remains under that parent. A later explicit new submission has a new parent. Existing TTS resume/retry of the same job keeps its parent but must retain prior failed attempt events once that telemetry exists; current latest-state fields cannot reconstruct them.
- Operation = a logical stage such as embedding, search, chat, chunk generation, media output generation or audio merge. Operation IDs are stable within the parent; attempts refer to them. Chunk display uses index + 1 while retaining the stored zero-based index. Missing correlation means an incomplete detail view, not permission to invent children.
- Attempt = one actual outbound generation/search/embedding invocation, including failures with consumed usage. No-eligible-key/model skips are selection events, not provider calls. Poll/status/stream/URL-renewal HTTP calls and internal merge/upload work are not new generation calls; optionally show them separately as transport/internal operations. A remote task submission plus polling is one generation attempt, not one attempt per poll.
- Stable identities: `query:<id>`, `tts:<job_id>`, `media:<id>`; child operation/attempt IDs scoped to the parent. `request_id` is correlation only, never an ownership or uniqueness guarantee. Do not merge matching text/time/user into a workflow. If a frontend action sends several independent generation/review requests, grouping requires an explicit trusted workflow identifier in a later telemetry change.
- Request types: `query`, `generate`, `tts`, `image`, `video`. Model types: `chat`, `embedding`, `tts`, `image`, `video`. Standalone embedding/search needs a recorded standalone parent; otherwise it is a query child. Model registration alone is not a request.
- Attribution separates `authorized_app_id`, backend owner and any external app-user reference. Existing app association cannot identify an individual frontend user. A future external-user reference must come from an authenticated app's trusted server, be scoped to that app, and follow its ownership controls; do not mix frontend user IDs with backend `users` or dashboard `memberships`. Missing/legacy identity is shown as not recorded. New attribution/correlation plumbing is a Phase 5 gap, not a completed Phase 1 feature.

**Normalized fields and statuses (read contract, not new columns)**

- Parent summary: composite/native ID, source/request type, created time, source and normalized status, recorded selected/successful model/provider when known, attribution, elapsed time with provenance, recorded token metrics, known provider cost/coverage and customer accounting status. Keep missing values null. A failed request with no successful model displays no successful model, not a fabricated model named `failed`.
- Operation/attempt detail: parent/operation IDs, kind, chunk/output index if applicable, attempt index if recorded, requested/actual model and provider if known, status, elapsed time, nullable input/output tokens/cost with source, safe error code/type and HTTP status if recorded. Never expose raw keys; any key reference must be safe admin-only metadata. Do not manufacture detailed attempts from a counter.
- Status mapping: `pending` -> queued; processing -> processing; `success`/completed -> completed; `timeout`/failed -> failed; cancelled and rejected stay distinct. Unrecognized/missing legacy statuses -> unknown, retaining the original value. A recovered retry may complete its parent while earlier failed attempts remain failed. Historical terminal transitions require events and cannot be inferred from the latest row.
- For async work, HTTP 200/202 means accepted, not completed; job state determines result. Partial output with a failed parent is still failed unless an explicit partial-success policy is separately introduced.

**Counting, timing and graphs**

- One failed parent with 5 chunk operations and 6 actual generation attempts counts as 1 user request, 5 chunks and 6 provider calls; merge is an internal operation. A failed primary followed by a successful fallback counts as 1 completed parent, 2 attempts and 1 failed attempt. Unique users is a separate metric and unavailable without reliable user attribution.
- Table and graph parent filters use the same source projection. Date range is `[from, to)` in UTC, with daily parent cohorts based on parent creation time and deterministic ordering by created time + composite ID. Parent graphs count current status, not historical status transitions. Retry/attempt graphs state that they cover the selected parent cohort; chronological provider-call graphs require real attempt timestamps in a later phase.
- Parent model filter means final successful model when recorded, otherwise recorded selected model. Attempted-model/provider breakdown is separate and may include the same parent under several attempted models; it must never be summed as unique parent requests. Legacy model/provider values derived from the current catalog are labeled catalog-derived, not execution snapshots.
- Success rate = completed / (completed + failed); pending/processing/cancelled/rejected/unknown are separate counts. Attempt failure rate uses only recorded terminal provider attempts and shows coverage. Missing attempt telemetry is not zero attempts.
- Parent elapsed time is wall-clock start-to-terminal duration, including queue/merge when those boundaries are known. Never sum parallel attempt durations as parent latency. Query duration/media elapsed are recorded metrics with their existing scope; TTS updated-minus-created is only a labeled estimate until immutable boundaries exist. Audio/video playback duration is a different metric. Unrecorded timing is excluded from averages with a sample count.

**Failed history, observed usage and customer accounting**

- Keep failed parent and attempt records, safe failure reason, trustworthy attribution and observed consumed tokens/cost. A failed request is visible in history even when customer-chargeable tokens are zero. Do not discard or overwrite provider usage to enforce the billing rule.
- Maintain distinct views: observed provider usage/expenditure, customer-chargeable usage, and accounting state. A terminal failed/cancelled/rejected parent contributes zero customer-chargeable tokens. Queued/processing work has no finalized charge. For a completed parent, failed attempts never contribute customer-chargeable tokens; successful work follows the existing applicable accounting policy. TTS/media token billing cannot be invented from character count, duration or file size.
- Current query/generate accounting is app-level (`app:<authorized_app_id>`) and guards completed non-cache results. It is not proof of frontend end-user billing or universal TTS/media accounting. Report existing accounting status/provenance; a future UI must distinguish the zero-charge policy from an absent or unverified ledger. Completed accounting must be idempotent so replay, job resume or viewing history cannot duplicate charges.
- For observed cost, sum each unique recorded provider call once, including failed calls. Do not add parent aggregate + child costs, or trace attempts + the same provider_calls again. Surface known subtotal, missing cost count, completeness and estimate source; an incomplete subtotal is not a full bill. No-call skipped attempts and cached chat execution are not new chat charges; newly executed search/embedding is still provider usage.
- Failed events must remain inspectable across retry/resume once event telemetry is implemented. Unauthenticated rejected calls have unknown customer attribution; never accept a caller's user ID as proof. Record persistence can itself fail, so current history cannot be advertised as lossless; durable retry/event coverage needs separate implementation and tests.

**Phase 1 acceptance cases for subsequent implementation**

| Scenario | Expected history/metrics |
| --- | --- |
| 5 TTS chunks, one provider retry, completed job | 1 completed parent; 5 chunk operations; 6 provider calls only if all recorded; merge separate; absent attempt telemetry marked incomplete. |
| Primary fails then fallback succeeds | 1 completed parent; both attempts visible; observed failed usage retained; no failed-attempt customer tokens. |
| Every model fails after consuming reported tokens | 1 failed parent; attributable errors/observed usage retained; zero customer-chargeable tokens. |
| Same failed TTS job resumed successfully | Same parent; current status completed; prior failure events retained when recorded; accounting exactly once; no invented historical events. |
| Cached chat answer | 1 completed parent; zero new chat-provider calls/tokens/cost; separately observed new embedding/search is not erased. |
| Parallel chunks | Parent elapsed reflects wall-clock boundaries, not sum of chunk elapsed values. |
| Two apps share a backend owner | Do not attribute old media rows to either app without a persisted app link; no frontend-user inference. |
| Same text sent twice / review via a separate API call | Distinct parent IDs; no automatic merging by text or correlation header. |
| Pre-handler rejection / persistence interruption | Safe minimal attributed-or-unknown record where available; zero customer charge; coverage limits visible. |
| Model/date/status filters + pagination | Parent chart counts match all filtered table records, not just the current page; attempt totals explicitly separate. |

Phase 1 exit: source inventory, hierarchy, field/status/counting/billing contracts, identity gaps and acceptance scenarios are documented. Phase 2 implements read-only coverage from existing sources; per-provider TTS/media attempts, immutable timing, trusted frontend-user attribution and missing workflow grouping remain Phase 5 work. No migration, provider call, data rewrite, legacy retirement or runtime billing change was performed for Phase 1.

### Phase 2 API contract and verification (2026-10-19)

All three routes require an approved, active **administrator dashboard session** in `Authorization: Bearer <dashboard-session>`; a customer app key is not a dashboard credential. Successful responses have `Cache-Control: no-store`.

- `GET /api/v1/dashboard/requests?page=1&page_size=20&request_type=tts`
- `GET /api/v1/dashboard/requests/summary?request_type=tts`
- `GET /api/v1/dashboard/requests/tts:<job_id>` (also `query:<numeric-id>` or `media:<numeric-id>`)

List and summary accept the same filters: exact `request_type`, normalized `status`, `model`, `provider`, `authorized_app_id`, `owner_user_id`, and `date_from`/`date_to`. Phase 3 adds optional `search` (1–128 characters), a literal substring over request ID/model/provider with SQL wildcards escaped. Omit a filter to include all values. API dates require a timezone; range is inclusive start/exclusive end, normalized to UTC, defaulting to the last 30 days and bounded to 366 days. The UI accepts valid YYYY-MM-DD calendar dates and converts them server-side to UTC midnight. List page size is 1–100; malformed filters return 422 and missing detail IDs return 404. Summary is registered before the dynamic detail route.

List returns `page`, `page_size`, `total`, effective dates and `items`. Each item contains the common contract fields, safe attribution and metric provenance. `external_user_id` stays null on legacy records or absent app-provided references; Phase 5 stores the optional authenticated app reference without interpreting it as a gateway owner. Failed/cancelled/rejected items report `customer_chargeable_tokens: 0` with `customer_tokens_basis: zero_charge_policy`; this reports the approved policy, not a newly reconciled customer ledger. Completed customer tokens remain unreported instead of inferring billing from provider usage. TTS elapsed time uses a recorded terminal boundary when available, labeled submission_to_latest_terminal; unmeasured legacy/running jobs stay null. It is never derived from mutable updated_at. Actual audio duration is available separately in detail.

Summary returns parent `requests`, status/type/model/provider breakdowns, terminal success rate, average elapsed time with sample count, recorded input/output token sums with sample counts, known provider-cost subtotal with cost samples and complete-cost-request count. `token_scope` explicitly identifies latest-final-response usage, not total provider-attempt consumption; failed-attempt observations remain available in query detail. Phase 4 adds `series`, containing the same metrics plus `day`, `completed`, `failed` and daily success rate; `series_scope` states UTC creation-date cohorts/current parent status. A 366-day range can contain at most 367 calendar buckets when both edges are partial days. Empty days have zero request counts and null unmeasured timing/tokens/cost/rate. Global timing remains weighted by measured requests, not an average of daily averages. PostgreSQL grouping reuses one timezone expression/bind in SELECT/GROUP BY. Known subtotal is not a full provider bill. Summary covers all matching pages; provider-attempt totals are not fabricated from incomplete telemetry.

### Phase 4 graph usage and checks (2026-10-20)

PostgreSQL correction (2026-10-20): mixed history reused a bound `None` across float and boolean UNION columns, causing list/summary failures despite SQLite tests passing. Use SQL `NULL` in the shared projection. Read-only verification on configured PostgreSQL now returns 80 parents from both list and summary; 22 isolated history/analytics tests pass, including an asyncpg compilation regression. No migration or data changes. Browser verification was unavailable because the Edge control connection timed out; refresh the running backend/page to load the correction.

- Open `/requests`, keep **Display: Table and graphs**, apply the existing filters, then select **Daily metric**. Use expandable **Counts by model, provider, type and status** and **Exact daily figures and sample counts** for breakdowns/tabular values. Pagination does not limit the graph's scope.
- Every recorded line point and every bar has a visible numeric label. Zero counts remain 0; unavailable metrics remain N/A/Not recorded. Short charts resize to their container; dense ranges retain labels with keyboard-focusable horizontal scrolling. Small nonzero USD estimates use scientific notation below six decimal places rather than appearing as zero. Display values are formatted; estimates remain estimates.
- `components/chart.tsx` was extracted from the existing Overview chart after reference checks. `request-analytics.tsx` maps metric definitions to it; no per-model chart files or added dependencies. Existing cards/DataTable remain reusable. Client components receive validated aggregate data only; gateway calls/session handling stay in `.server.ts`.
- Table and summary share all filters and identical UTC boundaries, including blank-date defaults. `view=table` skips aggregation. Summary 404/5xx/invalid shapes show a safe notice without hiding the table; 401/403 still fail closed. No prompt text, secrets or raw backend errors enter graph data. Separate HTTP reads are not a transactional historical snapshot; active status updates can occur between them.
- Verification: **31 frontend tests**, **21 isolated history/Overview analytics tests**, typecheck, production build, targeted Ruff/Python compilation and diff checks passed. Tests cover visible numeric/zero labels, null gaps, dense labels, tiny nonzero costs, all-page filters, same date boundaries, malformed/old/error summaries, table-only mode, daily totals, weighted timing, UTC/max-range boundaries and PostgreSQL grouping compilation. Fabricated component browser checks cover metric switching, bar counts and a 390px viewport with 90 retained data points and no page overflow. This is not configured PostgreSQL/provider or deployed end-to-end acceptance. Phase 3's document-hydration warning remains a tooling-specific observation; the Phase 4 component preview mounted inside its own container.

Detail returns input/output where recorded, whitelisted query attempt metadata, ordered TTS chunk checkpoints/counters, or media dimensions/output count. Versioned instrumentation marks coverage only when supported and terminal; legacy/interrupted coverage remains incomplete. It never adds trace attempts and provider_calls as separate invocations. TTS/media errors currently expose a safe presence flag, not raw provider exception strings. Raw configuration, key values, lease tokens, object keys and signed/result URLs are omitted; use existing authorized output-access flows.

The `/requests` admin frontend now calls the shared API through its Remix server loader. New backend routes can also be inspected in local `/docs` after backend reload/restart; no new app grants or migrations are needed. Existing records become readable without backfill writes. Phase 2 verification: **106 isolated history/query/reliability/dashboard/app-authorization tests passed**, including PostgreSQL SQL compilation. Phase 3 verification: frontend typecheck, **25 frontend tests**, production build, **14 isolated history tests** (including shared search/list-summary totals), targeted Ruff and diff checks passed. Isolated fabricated-data browser checks covered TTS detail/chunks, failed attempt usage/zero-charge labels, keyboard navigation, filtering, pagination, empty state and a 390px viewport with table-local scrolling and no page overflow. Browser tooling injected a comment root outside the document body and produced hydration warnings in that preview; this is not evidence of an application-origin hydration defect or a clean live-browser acceptance run. Tests requiring local sockets ran outside the Windows sandbox. Configured/production PostgreSQL execution, real provider generation and deployment remain unverified.

## Unified model endpoints: additive phase (2026-10-18)

`model_endpoints` now holds shared execution settings for chat, TTS, image, video and embedding configurations. Identity, provider and pricing stay in `ai_models`; encrypted keys and counters stay in `provider_api_keys`. Each endpoint has an `ai_model_id` foreign key, model type, adapter, complete `endpoint_url`, optional LiteLLM `api_base_url`, active status, configuration JSON, `created_at` and `updated_at`. One configuration per model/type is enforced. API updates refresh `updated_at`; an ORM update also uses SQLAlchemy's database-time onupdate. Provider-key selection reuses the model's provider pool; no endpoint is pinned to one key, and provider-name matching is still a logical association rather than a new provider-registry foreign key.

**No legacy model table has been removed.** Transitional `legacy_tts_model_id` and `legacy_media_model_id` foreign keys preserve voices and media-generation identities. The migration imports legacy identities into the canonical catalog only when absent, preserves existing prices/status and rejects conflicting providers or inline credentials before DDL. Imported rows have no inferred paid pricing: their zero prices require manual pricing review. It copies the four TTS and five media configurations using their existing URLs and parameters, with creation/update timestamps for the new configurations; legacy model rows, voice mappings, key identities and media history are preserved. No chat/embedding capability or provider URL is guessed for existing catalog-only models.

Runtime reads prefer linked endpoint settings, then use the legacy configuration only when no endpoint exists. A disabled endpoint/canonical model does not fall back to an active legacy row. TTS keeps the local-Kokoro block, cached samples and voice IDs. New jobs capture the effective URL/settings; old job snapshots keep their saved configuration. Media keeps legacy audit IDs, uses full endpoint URLs and reserves a provider-key slot for each generation submission (including fan-out); polling reuses the selected key. An explicitly configured legacy environment-key reference remains a fallback when no DB key is eligible. Gemini keeps its existing key rotation; linked remote Kokoro can use its matching provider pool while retaining the existing environment fallback. Existing provider labels are preserved, not silently translated to hosting-provider names.

Query and direct-text generation share optional LiteLLM base configuration. Explicitly configured non-chat models are excluded from chat selection. Embedding endpoint settings can be stored, but semantic embeddings still use the existing EMBEDDING_MODEL configuration; this phase does not replace their selector or add new inference adapters.

Admin APIs (approved dashboard session, not an ordinary app key):

- `GET /api/v1/dashboard/model-endpoints?ai_model_id=...`
- `POST /api/v1/dashboard/model-endpoints`
- `PUT /api/v1/dashboard/model-endpoints/{endpoint_id}` (full configuration replacement; identity/type cannot be reassigned)

Mutations require the configured dashboard Origin. Responses are no-store, contain no keys, and reuse the existing audit trail for actor/action tracking. URLs cannot contain inline credentials, query strings or fragments; settings reject secrets and duplicate identity/pricing fields. New TTS/media configurations create only a compatibility model shell when necessary; effective settings remain in model_endpoints. Supported configuration storage does not imply a new provider adapter has been implemented. Use the existing supported TTS/media handlers.

Example chat configuration after selecting its existing AI model ID:

```json
{
  "ai_model_id": 123,
  "model_type": "chat",
  "adapter": "litellm",
  "endpoint_url": "https://provider.example.test/v1/chat/completions",
  "api_base_url": "https://provider.example.test/v1",
  "is_active": true,
  "config": {}
}
```

Use real administrator-configured URLs; the example is a placeholder. LiteLLM takes the base URL, not the complete operation URL. Provider adapters must support the configured base; adding a URL does not make arbitrary protocols OpenAI-compatible. TTS defaults use `default_voice`, `default_lang`, `default_speed`, `is_local` and `provider_config`. Media uses existing version/default parameters, response extraction and polling settings. Secrets belong exclusively in the existing key flow.

Migration `e610a2c947bf` follows `d920f41a672e`. Configured PostgreSQL upgrade was applied on 2026-10-18: **9 endpoint rows and 9 catalog imports**, timestamps verified; old TTS/media rows, voice links, existing catalog rows, key identities and media history were verified preserved. No endpoint grants were changed. Redeploy/restart the backend separately. Downgrade drops only the newly introduced endpoint table and leaves imported catalog rows to protect subsequent references; it never drops the legacy model tables.

Verification: the final full isolated backend suite passed **373 tests** (including other current checkout features), with mocked providers; endpoint migration/CRUD, timestamps, ownership controls, custom chat base, TTS snapshots/voice IDs and media key routing are covered. Targeted Ruff, compilation and diff checks passed. Configured read-only catalog smoke returned 3 active TTS models, 1 image model and 0 video models without sending provider requests. These counts preserve existing activation/local-inference rules and do not claim real synthesis/media validation.

Manual acceptance before any later retirement:

1. List the nine imported configurations; check URLs, provider pool labels and canonical IDs against both old catalogs. Review newly imported pricing rather than treating zero as verified free usage.
2. Edit a configuration through the admin API; check updated_at changes while created_at, canonical identity and existing key counters remain stable. Do not expect edits to linked old configuration rows to override the new configuration.
3. Test custom-base chat through query and direct generation, including fallback/error/history. Test supported TTS sample/job/live preview, voice listing, retry and completion with existing IDs; confirm old jobs retain snapshots and local inference stays blocked.
4. Test image/video generation and async polling, multiple outputs and provider quota exhaustion. Confirm complete URL use, per-submission key counts and existing media-history IDs. Legacy key fallback is transitional, not a claim that every old environment credential was migrated.
5. Disable an endpoint/canonical model and confirm the old row cannot reactivate it. Check unauthorized/non-admin/foreign-origin management requests are rejected and no API keys appear in configuration responses.
6. Confirm pricing, ownership, credits and library/history still match before/after. Only after explicit manual acceptance plan a separate migration to rewire voice/media foreign keys and retire old tables. No automatic retirement is included here.


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

## Prompt-only generation and shared records (2026-10-18)

Next.js servers can call `POST /api/v1/generate/` with a complete final prompt.
The existing `POST /api/v1/query/` retains its search/query flow and 4,096-character
input limit. Direct generation performs no automatic search, retrieval, tools,
prompt rewriting or quality-repair calls.

Request:

```json
{"prompt":"Complete final instructions and source data prepared by Next.js"}
```

Response:

```json
{"answer":"Complete cover-letter text...","title":"Frontend Developer — Azeemlab","request_id":"nextjs-123","finish_reason":"stop"}
```

Only `prompt` is accepted; empty/blank/invalid-Unicode input and extra fields are
rejected. The provider's text is preserved. `finish_reason` is `stop` only when
the provider explicitly reports normal completion; otherwise usable text has
`unknown`. Known incomplete answers, refusal and empty/malformed output return errors.

`title` is always included as a string or null. For an initial cover letter, the
same model call requests a 3–7-word title, at most 80 Unicode code points, in the
prompt's requested output language using only the supplied role/company. Backend
validation rejects contact data, applicant-name sources and ungrounded substantive
words. When evidence, language-safe wording or valid metadata is unavailable,
`title` is null; the valid letter still succeeds. No extra required input field or
title-generation call is added. The internal JSON envelope/title sources never
appear in the public answer, and only the validated title is saved in existing
final-response metadata. Review/repair requests do not require a title. If only
trailing optional metadata is truncated after a completely decoded answer string,
the letter survives with null title and unknown completion status; unfinished
answer strings still fail as truncated.

Next.js should validate the optional title before saving it to the existing
`conversation.title` and sidebar. Apply it only for the first successful generation;
never overwrite a manual rename. Missing/invalid titles retain the current
prompt-derived fallback. These frontend conversation rules remain the consuming
application's responsibility; the gateway does not manage conversations.

Title update verification (2026-10-18): **187 selected isolated backend tests passed**,
including 37 optional-title tests and the existing generation/history/ownership
regressions. Providers were mocked. This verifies parsing, validation and storage,
not live multilingual title quality or consuming-frontend conversation updates.

Authenticate with a server-only `X-API-Key` or existing Bearer support. The key's
app must have the exact `POST /api/v1/generate/` grant; a valid key without that
grant returns 403. Origin is not required for current service-key authentication.
Optional `X-Request-ID` accepts 1–128 ASCII letters/digits or `.`, `_`, `:`, `-`;
invalid/missing values become a UUID. It is echoed in JSON and the response
header for tracing, without duplicate suppression. Responses use `no-store`.

| Server setting | Default |
|---|---:|
| `GENERATION_MAX_PROMPT_CHARS` | 32,768 Unicode code points |
| `GENERATION_MAX_BODY_BYTES` | 524,288 bytes (512 KiB raw JSON) |
| `GENERATION_MAX_OUTPUT_TOKENS` | 2,048 tokens |
| `GENERATION_TIMEOUT_SECONDS` | 45 seconds of processing |
| `GENERATION_PROVIDER_TIMEOUT_SECONDS` | 20 seconds per provider call |
| `GENERATION_MAX_CONCURRENCY` | 4 in-flight generation handlers/process |
| `GENERATION_RATE_LIMIT` | 30/minute per client IP/process |

Shielded DB finalization has a separate bounded 10-second cleanup window. Model
selection and DB key reservations reuse the existing server-controlled routing;
at most four provider calls occur. SDK/LiteLLM retries are disabled, and ambiguous
timeout/connection loss is not automatically retried through another model.
Context checks conservatively reserve UTF-8 input and envelope-instruction bytes plus 256 framing tokens
and the output allowance against known model capacities; input is never truncated.
The character cap does not guarantee a particular model's context capacity.

**Shared storage:** valid generation prompts and provider text use the existing
`queries` and `final_responses` tables. Metadata identifies `request_type: "generate"`
and saves app/key ID, request ID, status, timing, internal model, fallback attempts,
available tokens/cost and normalized finish/error details. No new table, column or
migration is needed for this feature, and no dummy search results are saved.
Known partial text is retained as failed output, not returned as successful text.
Rejected bodies and secrets are omitted; unknown costs remain null. Prompt/answer
text is persisted in the database rather than ordinary operational logs.

Both request types appear in the existing query-record APIs described below and
the admin `/requests` page. Customer A cannot read customer B's list/detail records:
API access uses verified app ownership and separate exact history GET grants.
Authorized administrators can view all records; non-admin dashboard members cannot
use the admin APIs. A shared Next.js service key identifies one app, so Next.js
must still enforce its end users' session/record ownership separately.

Generation rows are excluded before the query DB-cache candidate limit, preserving
query caching. The initial processing row is committed before provider work;
success, failure, timeout and cancellation finalize that record. A final-save
failure returns 503 `record_persistence_failed`, with stale rows handled by existing
recovery. Database outages still limit audit durability. The existing 30-second
maintenance loop reconciles pending successful usage exactly once and marks
processing rows older than ten minutes failed without repeating provider calls.
Failed/cancelled requests never add successful usage or deduct frontend credits.

Verification on 2026-10-17: **150 selected isolated backend tests passed**, including
41 generation and 16 shared-record tests, with mocked providers and local SQLite.
Targeted lint, formatting and diff checks passed. This is a scoped historical result;
the later dependency-cleanup result above is separate. No live provider quality,
production persistence or deployment is established by these mocked tests.

Remaining rollout: restart/deploy the backend, configure a compatible active DB
model/provider key, verify generation/history grants, and update the Next.js server
adapter to send `prompt`. For this new route, replace its old 4,096-code-point check
with the deployed generation cap (default 32,768), check serialized body bytes and
handle context/provider errors. Keep the old limit for clients still using `/query/`.
Authorized live checks should verify generation, saved history, ownership and the
required language quality. Frontend authentication, billing, credits, duplicate-charge
protection, templates and repair decisions stay in Next.js. Documentation updates
do not deploy the backend or change production permissions.

See [GENERATION_API.md](GENERATION_API.md) for curl, error codes, the complete
contract, provider-limit evidence and the regression test command.

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
- Query processing has a 180-second deadline. A lifespan maintenance task starts immediately and runs every 30 seconds. In bounded batches of 100 it marks processing rows older than 10 minutes failed (`WorkerInterrupted`), aligns final-response metadata, flags incomplete cost coverage, and never reruns generation or charges failed requests. Database outages defer cleanup until connectivity returns. This recovers record state, not a lost provider response.
- Successful requests durably mark accounting pending before aggregation. A compare-and-set claim, the existing atomic usage upsert and the recorded marker share one database transaction. Maintenance retries pending completed requests using the request's original UTC date; failed/cancelled/cache requests are excluded. Deferred rows rotate so bad records do not starve newer entries. No automatic replay of historical recorded or unmarked rows.
- Admin `/requests` now uses the unified `/api/v1/dashboard/requests` list/detail APIs through a server-only loader, with existing table/card/loading/error UI. Query, direct generation, TTS and media share filters/pagination while retaining source-specific details and incomplete-metric labels. The older dashboard query-requests endpoints remain admin-protected compatibility APIs; they no longer feed this page. Ordinary customer/app keys cannot use either admin history API. App-scoped query APIs retain their existing contracts and SQL ownership isolation.
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
