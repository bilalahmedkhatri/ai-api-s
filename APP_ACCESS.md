# FastAPI app access aur Facebook ownership

## Customer API keys (2026-10-15)

Current customer flow: existing registration/login/approval -> administrator-visible
encrypted membership master key -> dedicated API Keys page -> master key required
only for the first successful issuance -> generated secret shown once.

API authentication now accepts the secret via Authorization: Bearer or X-API-Key
without requiring Origin/X-App-Origin. Active project, active API-enabled gateway
owner and method/route grants still apply. Customer keys additionally require an
approved, non-expired membership. Administrative APIs keep separate authentication.

New customer keys share an automatically created internal ownership project; no
origin registration is needed. Existing apps, keys, TTS jobs and Facebook tokens
retain their original assignments. Legacy endpoint grants remain in force.

Migration ab92d18e740c follows f73a9d20bc41. Apply it before restarting the updated
backend/dashboard. Existing approved memberships can be enabled in Members;
new approvals receive a master key automatically. See admin/README.md for the
contract and rollout steps. Isolated tests do not migrate the configured database.

The older exact-origin onboarding instructions and dated verification below are
historical. Origin remains relevant to optional legacy browser CORS registration,
but is no longer an API-key authentication requirement.


FastAPI ke owners sirf is gateway ki `users` table mein hain. Frontend user IDs,
sessions aur permissions ko is table mein import ya map nahi kiya jata.

## Request flow

Registered exact HTTP(S) origin + secret API key -> active/approved app -> active
FastAPI owner with `api_access_enabled` -> exact endpoint permission -> handler.
Missing/invalid credentials return 401; an endpoint without a grant returns 403.
No redirect is used, so POST bodies and uploads retain their normal behavior.

`Origin` takes precedence over `X-App-Origin`. Server clients send `X-App-Origin`;
Referer is only a fallback. Scheme, hostname and non-default port must match.
Subdomains and wildcards are not automatically accepted. Headers alone are not
proof of app identity: the secret key is mandatory and stays on the app server.
Keys in query strings are no longer accepted. The old
`REQUIRE_API_KEY_FOR_QUERY` setting cannot disable authorization.

Audio/catalog/sample/status/renewal, query, ingest, image, video, media and Facebook
API routers all share the same mandatory check. Browser preflight uses approved
app origins from the database, including owner permission. Admin/key/cron routes
use CRON_SECRET separately and fail closed if it is missing. OAuth login/callback,
health, OpenAPI/docs and the existing static-file mount retain their separate
behavior; they are not app API handlers.

## Tables

- `users.api_access_enabled`: admin-controlled gateway permission, default false.
- `authorized_apps.owner_user_id`: foreign key to FastAPI users only.
- App stores exact `allowed_origin`, approval/active flags, and endpoint grants.
- `access_apikey.authorized_app_id`: binds a hashed secret to one app.
- `facebook_page_tokens.authorized_app_id`: scopes page tokens to one app.
- Facebook uniqueness is `(authorized_app_id, page_id)`, including two apps owned
  by the same gateway user. The existing token `user_id` is set from the gateway
  app owner, never from frontend request data.

## Admin onboarding

Use the existing FastAPI Google login to create a gateway user. The administrator
then sends `X-Cron-Secret` (or admin Bearer secret) for these actions:

1. `PATCH /api/v1/apps/owners/{user_id}/permission`:
   `{"api_access_enabled": true}`
2. `GET /api/v1/apps/endpoints`: discover valid method/route templates.
3. `POST /api/v1/apps/`:

```json
{
  "name": "GenZee app",
  "owner_user_id": 1,
  "allowed_origin": "https://your-actual-site.example",
  "is_approved": true,
  "allowed_endpoints": [
    "GET /api/v1/audio/models",
    "GET /api/v1/audio/voices",
    "GET /api/v1/audio/sample",
    "POST /api/v1/audio/tts",
    "GET /api/v1/audio/tts/status/{job_id}",
    "POST /api/v1/audio/tts/renew-url",
    "POST /api/v1/facebook/store-token",
    "POST /api/v1/facebook/post-pages",
    "DELETE /api/v1/facebook/pages/{page_id}",
    "POST /api/v1/media/process-urls",
    "GET /api/v1/media/{item_id}",
    "DELETE /api/v1/media/"
  ]
}
```

The ID and address above are examples: use the real gateway user and site origin.

4. `POST /api/v1/keys/`:
   `{"client_name": "GenZee app", "authorized_app_id": 1}`.
   Save `raw_key` once. Listing returns metadata, not the secret.
5. Configure the frontend **server** environment:
   `GATEWAY_API_KEY=<issued-secret>` and
   `GATEWAY_APP_ORIGIN=https://your-actual-site.example`.
   Next.js routes send these app credentials through `gatewayHeaders()`;
   frontend owners and users remain entirely separate.
6. Reconnect Facebook pages through the authorized app. The frontend still checks
   its own user's page ownership before requesting gateway actions.

`PATCH /api/v1/apps/{app_id}` changes approval, activation or endpoint grants.
Owner and origin are immutable to avoid accidentally transferring connections.
Revoking the gateway owner permission blocks every owned app on the next request.
`POST /api/v1/apps/{app_id}/link-key/{key_id}` explicitly links a legacy key.
Keys already assigned to another app cannot be transferred through this endpoint.

## Facebook lifecycle

Store, refresh, publish and disconnect select the page by both page ID and app ID.
Tokens remain encrypted. A page registered by another app returns 404 for publishing
or disconnecting; its token is not read or modified. Reconnecting only updates the
current app's token. Facebook error code 190 marks that connection inactive and
returns a reconnect message. No frontend-user session grants gateway ownership.

## Migration and existing records

Revision `a71c942e106b` follows `de25f5fb5538` and was applied to the configured
PostgreSQL database on 2026-10-11. Existing keys and tokens are preserved with no
invented app assignment; those records cannot authorize requests until explicitly
linked (keys) or reconnected (Facebook). Existing gateway users default to no API
grant. At migration time, the database had 0 gateway users, 0 registered apps,
69 keys and 10 Facebook tokens. This is a historical snapshot, before Google
owner sign-in and the explicit onboarding below. Legacy records remain unassigned.

## GenZee onboarding completed (2026-10-11)

Existing gateway Google user `bilal.azeemlab@gmail.com`, ID `1`, has
`api_access_enabled=true`. This identity is not a frontend account mapping.

| Environment | App ID | Key record ID | Exact approved origin | Gateway base |
|---|---|---|---|---|
| Local | 1 | 70 | `http://localhost:3000` | `http://localhost:8000` |
| Production | 2 | 71 | `https://www.genzee.video` | `https://ai-api.fastapicloud.dev` |

Both apps are active and approved, with 13 method/route grants (including TTS retry) for voice,
media and Facebook integration, including Facebook disconnect. Query, ingest,
image/video generation and administrative endpoints were not granted to these apps.
The apex domain `https://genzee.video` is a different origin; it is not implicitly
authorized by the `www` registration.

Provisioning is repeatable:

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
.venv\Scripts\python.exe -m scripts.bootstrap_genzee_apps --owner-email bilal.azeemlab@gmail.com --frontend-root D:\voice_generator_free\free_voice_generator
```

The first run created two keys; the second reused both apps and keys and created
zero additional keys. Raw keys are saved only in the frontend's git-ignored
`.env.local` and `.env.production.local` alongside `GATEWAY_APP_ORIGIN` and
`VOICEOVER_API_URL`. They are not included in these documents or terminal output.
Unrelated existing frontend environment settings are preserved.

For hosted production, install the production file's three gateway values into
the hosting provider's server environment and redeploy. Restart/redeploy the
gateway with the updated code and its own Google OAuth configuration. Local file
changes do not update a remote service. No hosted deployment was performed.

Reconnect each authorized Facebook Page through the frontend's normal Facebook
flow to create its app-scoped gateway connection. Existing legacy tokens were
preserved, never reassigned. Real Meta consent/publishing has not been exercised.

Downgrade refuses to restore old user/page uniqueness when app-scoped connections
would collide; an admin must resolve those conflicts first.

## Verification

The backend test suite uses isolated SQLite databases and mocked providers/Facebook.
The authorization tests execute real SQL joins and ASGI requests without bypassing
authentication. Regression handler tests use an explicit auth override to exercise
their existing validation logic independently. No real Facebook posts are published.
Production migration verification uses read-only queries for revision, columns,
foreign keys and uniqueness constraints.

Completed verification: 123 backend tests and 38 frontend ownership/integration
tests passed; frontend TypeScript checking passed. Facebook API interactions were
mocked. The changed gateway authorization modules passed Ruff. Existing library
deprecation warnings remain unrelated to access authorization.

Additional provisioning tests cover key reuse, local/production separation,
missing owners, invalid endpoint grants, conflicting origin ownership, and atomic
environment-file writes. Configured-database ASGI checks exercise the actual
gateway app and registrations; they do not prove that hosted services have deployed
this version. Local and production keys returned 200 on the model catalog;
missing/wrong/cross-environment credentials returned 401, an ungranted query route
returned 403, and approved production CORS preflight returned 204. Public HTTP
checks from this workspace could not connect to the
local or production gateway, so deployed reachability remains unverified.

Next.js production compilation succeeded, but its final build worker could not
start in this Windows sandbox (`spawn EPERM`). Independent `tsc --noEmit` and
the frontend test suite passed. No production deployment or service restart was
performed; restart the gateway/frontend processes with the configured app
credentials to load the new code.


## TTS checkpoint phase (2026-10-13)

TTS creation, status, selective retry and URL renewal now enforce registered app ownership. Legacy unassigned jobs are retained but hidden from public app endpoints; frontend identities remain separate. Migration `e42b8d901c63` follows the existing dashboard revision. The scoped migration script appends the retry route grant to both verified GenZee origins while preserving existing grants and keys. See [TTS_CHECKPOINTS.md](TTS_CHECKPOINTS.md) for recovery, storage cleanup and deployment limits. Extracted media ownership remains outside this phase.

Live-preview migration `f53c9e012d74` follows that checkpoint revision. Both existing apps now have `GET /api/v1/audio/tts/stream/{job_id}`; bootstrap provisions 14 route grants. The stream uses the same app authorization and job scoping. Frontend stream access still requires its separate user history or cookie-bound guest proof; gateway ownership never comes from a frontend user ID.


## Daily provider-key reset

Use `POST /api/v1/cron/reset-daily-keys` with the cron secret; GET is no longer supported. Set QStash's destination method to POST. Optional `?provider=gemini` scopes the reset to that provider (case-insensitive); omitting it resets all providers. Empty or invalid identifiers return 422; an unknown provider affects zero rows. The existing reset sets requests_today=0 and is_active=true, including manually disabled keys; total_count is preserved. Repository reference checks found no runtime GET callers; external scheduler configuration must be checked separately. Deploy the backend change before testing the production route.

## Admin-only provider key allocation

`POST /api/v1/keys/provider/{provider}/claim` returns a JSON array containing **only raw provider key strings** (up to two). Authenticate using the existing admin `X-Cron-Secret` or `Authorization: Bearer <admin-secret>`; ordinary app/customer keys cannot call it. Use a trusted server client over HTTPS; never place this credential flow in a browser bundle. Responses use `Cache-Control: no-store` and `Pragma: no-cache`; the route never logs returned secrets.

Eligible rows match the normalized provider, are active, and have remaining daily quota (or no daily limit). Ranking is ascending `total_count`, ascending `requests_today`, ascending priority, then descending ID (newest tie wins). Selection and both increments occur in one SQL UPDATE with a locked selection on PostgreSQL (`FOR UPDATE SKIP LOCKED`). Returned rows are decrypted before commit; a decryption/database failure rolls back the allocation and gives a generic 503. HTTP 409 means no eligible/unlocked key; one remaining eligible row returns one key. Provider identifiers are validated; invalid identifiers give 422.

`total_count` is a lifetime **allocation counter** for this endpoint, not a provider-billing or network-attempt counter. Daily reset preserves it. Each returned row increments `total_count` and `requests_today` once, even if its later external provider call fails. Clients should not call a second accounting operation for the same reservation. Existing internal TTS/keyword flows are not wired through this allocation endpoint and retain their existing counting behavior. HTTP delivery and database commit are not atomic: a disconnected client or repeated POST may consume another allocation; this minimal endpoint does not provide idempotency tokens or leases.

Migration `c84f10d2a639` adds `provider_api_keys.total_count` as non-null BIGINT with a database default of zero; legacy and newly inserted rows start at zero. Its parent is `ab92d18e740c`. Apply the migration before serving the new ORM model. Existing provider key list endpoints continue their current metadata-only contracts.

Verification: `tests/test_provider_key_claim.py` covers selection, counter increments, ordering/ties, provider isolation, inactive/quota exclusion, single/empty pools, authorization, secret-only/no-store responses, corrupt-key rollback, daily reset, concurrent SQLite quota enforcement, PostgreSQL locking SQL compilation, and isolated migration upgrade/downgrade. PostgreSQL concurrency is not proven by the SQLite or compilation checks; live-provider requests are not part of these tests.

Allocation verification (2026-10-16): 81 isolated claim/authorization/customer-key/TTS tests passed, plus Ruff and compilation. With explicit user authorization, migrations ab92d18e740c and c84f10d2a639 were applied to the configured PostgreSQL database from revision f73a9d20bc41. Final revision c84f10d2a639, membership columns/project foreign key/unique constraint, and total_count BIGINT NOT NULL DEFAULT 0 were verified. No real provider keys were fetched or claimed, and no live provider call was made. The isolated tests were completed before this schema deployment; they were not rerun against the configured database.
