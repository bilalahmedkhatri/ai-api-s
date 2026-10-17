# Direct prompt generation

Next.js server calls **POST /api/v1/generate/** (including the trailing slash).
The existing `/api/v1/query/`, its 4,096-character schema, search behavior and
response contract are unchanged by this feature. The backend lives in
`D:\AIModels_SearchAPIS`; no GenZee frontend changes are required here.

## Contract

Request JSON has exactly one required field:

```json
{"prompt":"Complete final instructions and source data prepared by Next.js"}
```

`prompt` must be a strict, non-empty valid Unicode string with some non-whitespace
text. Extra fields are forbidden, including model, credentials, system messages,
language, history and applicant data. Prompt whitespace and line breaks are
preserved. URLs in prompt text remain text: nothing fetches or retrieves them.

Successful response:

```json
{"answer":"Generated text\nwith preserved line breaks","request_id":"nextjs-123","finish_reason":"stop"}
```

`answer` is non-empty provider text, returned verbatim, without another model or
backend formatting. `stop` means the provider explicitly reported normal stop;
`unknown` means it supplied usable text without a recognized completion status.
Unknown does not guarantee completeness. Known length truncation, refusal and
tool requests fail instead of returning successful partial text. No reasoning,
model identity, raw provider payload or usage-accounting claim is returned.

Use server-only `X-API-Key` (or existing `Authorization: Bearer` support). Existing
active key/project/owner checks and exact method/path grants apply. No Origin
header is required by the current authentication implementation. Missing or
invalid credentials return 401, missing endpoint permission returns 403 with
the existing `detail` string and a `request_id`.

Optional `X-Request-ID`: 1–128 ASCII letters, digits, `.`, `_`, `:`, `-`.
Missing/invalid values become a UUID. The response JSON and `X-Request-ID` header
agree, including validation failures. This is tracing only: repeated IDs do not
suppress generation or charges. All endpoint responses use `Cache-Control: no-store`.

```bash
curl "$FASTAPI_BASE_URL/api/v1/generate/" \
  -H "X-API-Key: $FASTAPI_SERVICE_KEY" \
  -H 'X-Request-ID: nextjs-123' \
  -H 'Content-Type: application/json' \
  --data '{"prompt":"Write a concise professional introduction in French using only these facts: ..."}'
```

Keep `FASTAPI_SERVICE_KEY` exclusively on the Next.js server. Next.js retains
end-user auth, credits, idempotency, ownership/history, input evidence, templates,
language rules and quality/repair decisions. FastAPI adds none of those systems.

## Server defaults and model budgets

| Environment setting | Default | Meaning |
|---|---:|---|
| `GENERATION_MAX_PROMPT_CHARS` | 32768 | Python/Unicode code points, including whitespace |
| `GENERATION_MAX_BODY_BYTES` | 524288 | 512 KiB raw JSON bytes, including escapes and fields |
| `GENERATION_MAX_OUTPUT_TOKENS` | 2048 | Maximum provider completion budget |
| `GENERATION_TIMEOUT_SECONDS` | 45 | Processing deadline: body read, validation, auth, DB selection and provider work; DB finalization has a separate bounded 10-second cleanup window |
| `GENERATION_PROVIDER_TIMEOUT_SECONDS` | 20 | Each provider attempt, also enforced by asyncio |
| `GENERATION_MAX_CONCURRENCY` | 4 | In-flight generation handlers per process; excess returns 429 |
| `GENERATION_RATE_LIMIT` | 30/minute | Shared SlowAPI mechanism, per client IP, per process/default memory storage |

Restart to apply settings/schema/concurrency changes. Set reverse-proxy body and
timeout settings consistently. Deploy one worker for these process-local bounds;
these are not distributed quotas or provider-wide RPM guarantees. Multiple Next.js
requests behind one server IP share its service rate. The body cap checks both
Content-Length and streamed bytes before JSON parsing, even for chunked uploads.
An upstream server may impose a smaller cap. Deadline cancellation is cooperative;
it cannot undo upstream work already accepted by a provider.

Routing reuses `_pick_models` and `_model_name` from `model_router`, including its
current preferred model, active/chat capability filtering and candidate ordering.
Generation caps actual provider calls at four; unavailable keys and insufficient
contexts do not consume that call budget. It reuses provider-scoped DB keys with
atomic daily request reservations; no environment-key fallback or end-user credit
deductions are added.
Context-ineligible candidates are skipped before reserving a key.

The existing backend has no provider tokenizer integration. The new path uses a
conservative `len(prompt.encode('utf-8')) + 256` input-token estimate and reserves
the full output allowance inside the model's reported input window. It also
requires an explicit output capacity at least as large as the allowance. Unknown
capacities fail closed; no prompt is silently truncated. This estimate can reject
prompts a model could actually fit, especially emoji-heavy input. Provider context
errors can still occur if catalog metadata is stale or provider framing differs.

Inspection of the installed LiteLLM catalog on 2026-10-17 found:

| Catalog entry | Input tokens | Output tokens |
|---|---:|---:|
| gpt-4o-mini / gpt-4o | 128000 | 16384 |
| gemini/gemini-2.5-flash | 1048576 | 65535 |

These are local metadata examples, not confirmation that these models/keys are
active in the configured database or available from providers. The catalog fell
back to its installed backup because remote refresh was unavailable. Runtime
checks inspect each selected row's metadata. LiteLLM's schema distinguishes
[input/output capacities from the legacy max_tokens field](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.schema.json).

For Next.js clients moving to this new route, replace the old 4,096-code-point
check with the deployed `GENERATION_MAX_PROMPT_CHARS` (default **32,768**) and send
`prompt`, not `query/input_type/search_engine`. JavaScript counts code points using
`Array.from(prompt).length`; `string.length` counts UTF-16 units. Also check UTF-8
bytes of the serialized JSON against the deployed body cap. Neither character
limit guarantees model capacity; handle `no_eligible_model` and provider errors.
Clients still using `/query/` must retain that route's existing 4,096 limit.

## Failures and retry policy

Generation errors follow the existing `detail` envelope, adding stable codes:

```json
{"detail":{"code":"output_truncated","message":"Output budget exhausted; request a shorter output"},"request_id":"nextjs-123"}
```

| HTTP | Codes / behavior |
|---|---|
| 401 / 403 | Existing service authentication / endpoint-grant detail strings |
| 413 | `request_too_large` |
| 422 | `invalid_request`, `no_eligible_model`, `provider_refusal` |
| 429 | `rate_limited`, `concurrency_limited` |
| 502 | `empty_provider_result`, `invalid_provider_result`, `output_truncated`, `provider_connection_error` |
| 503 | `no_provider_key`, `provider_unavailable`, `generation_unavailable`, `record_persistence_failed` |
| 504 | `provider_timeout`, `request_timeout` |

Provider SDK and LiteLLM retries are explicitly zero (`max_retries=0`,
`num_retries=0`); the installed OpenAI adapter passes that value through. The only
fallbacks are the existing bounded DB candidate order after explicit provider
failures (including 429, which marks the key exhausted). Ambiguous timeout or
connection loss stops immediately; refusal, malformed/empty output and known
truncation also stop. No quality repair calls or tool execution occur. An overall
timeout may leave upstream generation running; Next.js must decide whether to
retry and must provide its own duplicate-charge protection. See LiteLLM's
[retry-layer documentation](https://docs.litellm.ai/docs/routing).

Operational logs contain request ID, internal selected model, sizes, elapsed time
and outcome/error category, without prompts, answers, secrets or raw exception
messages. Validation errors are sanitized before the legacy global validation
handler can log/echo their input. Provider callback logging is disabled per call
with `no-log`; do not enable SDK debug logging or global prompt/callback overrides
in production.

## Shared query records and ownership

Generation now persists in the **existing `queries` and `final_responses` tables**;
there is no new table, column or migration. `Query.raw_query` stores the full
validated prompt. `FinalResponse.generated_output` stores provider text, including
known partial text for failed/truncated attempts when available. Partial text is
never returned as a successful generation. No fake `search_results` rows are made.

Existing metadata stores `request_type: "generate"`, the endpoint, tracing/key ID,
canonical status, HTTP status, duration, normalized finish reason, sanitized error
type/code, model attempts and provider-call estimates. Available token/cost fields
use existing columns. Unknown usage/cost remains null, and incomplete cost coverage
is reported honestly. No provider credentials, raw errors, tool arguments or
reasoning fields are stored. The public generation response contract is unchanged.

The initial app-owned `processing` row is committed before provider work. Success,
failure, refusal, truncation, timeout and cancellation finalize that same record
with a linked final response. Cancellation cleanup is shielded and bounded to 10
seconds. If final persistence fails after generation succeeds, the API returns
503 `record_persistence_failed`; existing stale-request recovery later marks a
leftover processing row failed without generating again. A database outage cannot
guarantee DB audit persistence; sanitized operational logs remain the fallback.

Pre-handler auth/validation/rate/body-limit failures reuse the shared rejection
audit. Their bodies are omitted, and ownership is assigned only if the credential
was verified; unverified rows remain unassigned and admin-only. Legacy/unassigned
records are never attached to another customer by request parameters.

Read both query and generation records through the existing endpoints:

* `GET /api/v1/query/requests`
* `GET /api/v1/query/requests/{query_id}`

The existing exact read grants are required separately from generation permission.
List and detail SQL always filter by the verified key's `authorized_app_id`.
User/customer A cannot read customer B's records. Shared server credentials
identify the same app; Next.js must still enforce ownership between its end users.

The existing admin `/requests` page and `/api/v1/dashboard/query-requests` APIs
show both request types using the same tables. Their list and detail handlers
require admin authorization; non-admin dashboard members cannot use them to bypass
app isolation. Metadata identifies generation records; no new history route or
dashboard is required.

Generation rows are excluded in SQL **before the query DB-cache row limit** so
they cannot supply cached search/query answers or displace recent query candidates.
Generation itself does not read/write either query cache. The ordinary query
generation pipeline and its response contract remain unchanged.

Successful generation usage is marked `pending` for the existing 30-second query
maintenance/accounting loop, which commits its daily app/model statistics and
recorded marker atomically. It does not deduct frontend credits. Failed/cancelled
requests never contribute to successful usage totals, even if provider usage is
known. Maintenance already recovers processing rows older than ten minutes and
never reissues generation. These helpers now cover both record types.

## Verification and deployment

Run `python -m pytest tests/test_generation_records.py tests/test_generation.py
tests/test_query_async.py tests/test_query_records.py tests/test_query_reliability.py
tests/test_app_authorization.py -q`. Tests use isolated
SQLite, dummy service/provider secrets and mocked completions; no paid provider
generation is authorized or performed. Unicode round trips cover English, Russian,
Spanish, French and Portuguese; these verify the adapter, not writing quality.

Before use: deploy/restart the backend, provision at least one active compatible
DB chat model and provider key, and append `POST /api/v1/generate/` to the intended
project's existing endpoint grants using the existing admin flow. Preserve other
grants. This feature needs no new tables or migration; pre-existing pending
checkout migrations remain a separate deployment responsibility. Set Next.js's
server URL/key, update its adapter and limits, and handle the errors above.

Live provider support, refusal/truncation mappings, transport cancellation and
multilingual writing quality need separate authorized evaluation. No deployment,
production DB mutation or frontend update is part of this implementation.

Verified locally on 2026-10-17 after shared-record support: **150 tests passed**
across the six modules above, including 41 direct-generation and 16 shared-record
tests. Targeted Ruff lint, formatting of the changed generation Python files, and
`git diff --check` passed. The eight warnings are existing SlowAPI
`asyncio.iscoroutinefunction` deprecations. Windows sandbox socket-pair creation
blocked asyncio before tests began; the successful suite ran outside that
restriction with the same isolated DB and mocked providers.

Files added: `app/api/v1/generate.py`, `app/services/generation.py`,
`tests/test_generation.py`, `GENERATION_API.md`. Existing files adapted:
`app/api/v1/router.py` (registration), `app/api/v1/apps/router.py` (admin discovery
and grant validation), `app/core/app_cors.py` (shared protected-route handling),
`app/core/config.py` (settings), `app/core/middleware.py` (bounded generation trace
IDs). Pre-existing worktree changes, including query code, were preserved.

The shared-record extension adapts `app/api/v1/generate.py`,
`app/services/generation.py`, `app/core/middleware.py`, and
`app/services/rule_engine.py`, adds `tests/test_generation_records.py`, and updates
this document. Existing history/admin APIs, telemetry, accounting and maintenance
helpers are reused without creating tables or changing the query POST handler.
