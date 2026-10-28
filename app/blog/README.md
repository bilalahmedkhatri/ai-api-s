# Multi-app blog management

Blog targets are independent of gateway `authorized_apps`. The admin editor selects
a stable blog app key. Existing dashboard memberships/permissions control access;
gateway API registrations are not the list of blogs.

Post lists and counts use the authenticated membership. Non-admins can list, open
and edit only their own posts; admins retain all-post access. New posts record a
server-owned `metadata_json.created_by_membership_id`, preserved through updates.
Legacy ownership uses only successful, unambiguous `BLOG_POST_CREATE` audit targets
for the exact app/post ID; unassigned posts remain admin-only. The existing list
response includes unfiltered `counts.mine` and `counts.total` alongside the scoped,
filtered pagination total. No schema migration is needed; deploy backend and admin
together so the new list response contract is available.

## Media ownership and gallery update (2026-10-25)

Current app schema is `blog_0002`, adding nullable authenticated uploader attribution and an index. The upload popup now includes a paginated My images gallery, and the backend always scopes the list/count and upload-token reuse to the current membership. Existing unassigned assets stay unassigned and hidden from the gallery. See [MEDIA_OWNERSHIP.md](MEDIA_OWNERSHIP.md) for migration, validation and manual acceptance details. Earlier phase sections below are historical.

## Configuration

Use backend `D:/AIModels_SearchAPIS/.env` only. Fill the six URL variables from
[.env.example](.env.example). Leave the remaining apps blank until ready.
Legacy `BLOG_*_APP_ID` entries are ignored and no longer required.
Existing URL values have not been rewritten.

`config.py` is the separate blog-app catalog: azeemlab, ailettergen, bondsgame,
sikanderart, e_sahulat and genzee_video. Labels are centralized there. All targets
share the same connector and planned table contract; no per-app CRUD branches.
Changing the configured URL routes that blog key to its own database, never the
gateway DATABASE_URL. New keys require a catalog entry, env variable, frontend
schema entry and permission definition; arbitrary browser-supplied URLs are denied.

PostgreSQL only, using existing asyncpg/SQLAlchemy dependencies. TLS defaults to
require. Credentials must be URL-encoded. sslmode maps to asyncpg ssl; unsupported
libpq/client options channel_binding, target_session_attrs and charset are omitted
like the existing gateway. Other unknown query options fail closed.
Restrict production credentials to the blog schema/tables. Do not expose URLs,
raw driver errors, credentials or session tokens in browser props/logs.

## Status and explicit read-only verification

From the backend root:

```powershell
.venv/Scripts/python.exe -m app.blog
.venv/Scripts/python.exe -m app.blog --check
```

Default status is offline. The explicit check connects only configured targets
with a 20-second per-target bound, starts each transaction with
SET TRANSACTION READ ONLY, verifies read-only mode, executes SELECT 1 and checks
only the three planned table names in the public schema. It makes no schema/data
writes and never prints hosts, DB names, usernames, passwords or driver messages.
Unconfigured apps are skipped; failures report only exception types.
Connectivity does not prove write grants, schema compatibility or production readiness.

Historical Phase 1 verification on 2026-10-23: azeemlab, ailettergen, bondsgame and genzee_video all
connected read-only. None had blog_posts/blog_categories/blog_assets in public.
sikanderart and e_sahulat were blank/skipped. Existing similarly named tables in
other schemas were not inventoried. No consumer project source was inspected or changed.

## Backend boundary and permissions

`database.py` supplies a lazy async session by blog app key, never by a gateway
app ID. It does not create tables or commit on behalf of callers. Uncommitted work
rolls back on session close and engines are disposed. NullPool avoids idle pools
across six databases; future load measurements may justify bounded pooling.

`router.py` exposes GET /api/v1/dashboard/blog/apps through existing dashboard
sessions. The handler requires approved/active membership and the blog-editor page.
Non-admin members also require component scope blog.app.<key>. Admins retain the
existing authorization override. Only key/label/configured are returned.
The catalog does not connect to target databases. A configured flag is not a live
health check. No membership grants are created automatically.

The existing Team & permissions management can grant blog-editor and the individual
blog.app.* components. Page access alone does not grant any target to a non-admin.
Future create/update/publish handlers must independently enforce their operation
permissions and target scope. No delete handler or delete grant is provided.

## Admin UI

The actual editor is in D:/voice_generator_free/admin_dashboard. The server page
uses its existing requireUser/pageRequest/loadPage/backend boundary; browser code
does not call FastAPI or hold connection URLs. BlogWorkspace renders the permitted
app list, disables unconfigured options and opens the editor for a selected app.

Browser drafts remain local until Phase 5 DB CRUD integration. They are separated
by membership ID and blog app key; switching with unsaved edits asks before discard.
Existing ai_voice_blog_drafts data is not copied, reassigned or deleted. Explicit
legacy import remains later work. No app is preselected on first load.
Only the currently enabled locale (English) received the new message namespace.
No consumer website UI changed.

## Common target schema (Phase 2 applied)

Same format in every app DB, without cross-database foreign keys:

- blog_posts: ID, locale, title, backend slug, excerpt, category_id, content_json,
  content_schema_version, metadata_json, author_name, author_designation, optional
  author_avatar_url, cover_asset_id, status, version, created_at, updated_at,
  published_at. Unique (locale, slug); published slug stable after title edits.
- blog_categories: ID, name, normalized_name, backend slug and timestamps.
  Unique normalized name/slug, with same-field select/create and duplicate protection.
- blog_assets: ID, B2 object_key, public_url, verified media_type/size_bytes, optional
  dimensions/duration, original_filename, alt/caption and created_at.

Tiptap JSON is the only editable content representation; no HTML copy or Base64.
Validate allowed nodes, attributes, URLs, size and depth. Author values are a
server-created snapshot from the existing dashboard profile, no author table or
central user FK. Central memberships.designation is now nullable VARCHAR(160);
Phase 3 implements profile editing and server-owned author snapshots.
Metadata/canonicals require confirmed blog route/locale configuration, not guessed
domains. Each app DB is an independent transaction; there is no distributed commit.

B2 uses authenticated uploads to public blog storage and stable public URLs, not
signed downloads. Existing log/audio storage behavior stays separate. The editor
still needs an audio node. Public draft media is readable to URL holders.
B2 upload checks, orphan cleanup and live storage acceptance remain Phase 4.

## Remaining phases

1. Independent catalog, URLs, read-only connection confirmation and selector: done
   for the four configured apps; the two blank apps remain pending.
2. Completed on the four configured apps: versioned blog tables and central profile
   designation. No startup create_all or automatic all-app migration.
3. Completed locally: create/read/update APIs, slugs, metadata, categories, author
   snapshots, optimistic versions and write permissions. No delete endpoint.
4. Implemented locally: dedicated public B2 upload, app folders, validation,
   upload-token retries and explicit stale-orphan reconciliation. New credentials
   and live B2 acceptance remain pending.
5. Completed locally: authenticated DB save/load replaces browser persistence in
   the active editor; standalone legacy demo/import remain separate.
6. Admin acceptance and explicit legacy import. Consumer rendering, sitemap/cache
   integration and deployment remain separately requested work.

## Isolated checks

```powershell
.venv/Scripts/python.exe -m pytest --noconftest app/blog/tests -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_blog_catalog.py tests/test_dashboard.py -q
.venv/Scripts/ruff.exe check app/blog tests/test_blog_catalog.py
```

Tests use mocked engines, fake URLs and isolated SQLite, never configured app DBs.

## Historical Phase 1 verification (2026-10-23)

- 25 offline module tests and 14 isolated catalog/dashboard regressions passed.
- Actual Next admin: 34 tests, typecheck, production build and changed-file lint passed.
- Production-mode browser checks used a local mock gateway, fabricated sessions and
  localhost-bound test UI on separate ports. Four selectable apps/two disabled apps,
  app-separated saved drafts, cancelled unsaved switching, preserved legacy drafts,
  375px layout without horizontal overflow and zero browser-to-gateway calls passed.
- Unauthorized viewer saw the existing Next forbidden screen and no editor data;
  that streamed page retained HTTP 200. Backend catalog tests independently confirmed
  real HTTP 401/403 denial. Signed-out browser redirected to /login.
- Draft actions now wait for Tiptap initialization. Dirty state is reported during
  the edit/save event, avoiding effect timing during app switching.
- A test-only redirect loop from binding Next to 127.0.0.1 was resolved by binding
  the isolated server to localhost, matching Next's normalized rewrite hostname.
  No production routing/security code was changed for the test setup.

At the end of Phase 1, real app DB acceptance was limited to read-only checks.
At that stage no migrations, blog writes, B2 calls, provider calls, grants or
deployment had been performed. The Phase 2 evidence below supersedes schema status. Existing running admin/backend services were not restarted.
A running backend must reload the new catalog route before the updated UI can
consume it. Blog write acceptance remains Phase 3 work.

## Phase 2 migrations and acceptance (2026-10-23)

Applied and independently verified after commit:

| Database | Revision | Blog rows (posts/categories/assets) |
| --- | --- | --- |
| azeemlab | blog_0001 | 0 / 0 / 0 |
| ailettergen | blog_0001 | 0 / 0 / 0 |
| bondsgame | blog_0001 | 0 / 0 / 0 |
| genzee_video | blog_0001 | 0 / 0 / 0 |

sikanderart/e_sahulat remain unconfigured and were not migrated.
Central DB advanced only f820b1c943ae -> b1023c5d7e90, adding nullable
memberships.designation VARCHAR(160). No designation values were invented;
post-migration verification found zero non-null designations. The existing
membership model and /dashboard/me DTO expose this optional field. There is no
new author identity table, designation editing UI, or blog write endpoint yet.

Source/entry points:

- migrations/schema_v1.py: frozen schema; future changes require a new revision.
- migrations/versions/b001_blog_tables.py: independent revision blog_0001.
- schema.py: current public table objects, separate from central ORM metadata.
- migrations/schema-v1.sql: offline PostgreSQL SQL for review.
- migrate.py: explicit operator command selecting one configured app.
- central_migration.py: exact-parent, single-revision designation operator.
- phase2-verification.json: credential-free live post-commit schema report.
- Root alembic/versions/b1023c5d7e90_membership_designation.py: central migration.

Operator commands from the gateway root:

```powershell
.venv/Scripts/python.exe -m app.blog.migrate --sql
.venv/Scripts/python.exe -m app.blog.migrate --app azeemlab --action inspect
.venv/Scripts/python.exe -m app.blog.migrate --app azeemlab --action upgrade
.venv/Scripts/python.exe -m app.blog.migrate --app azeemlab --action verify
.venv/Scripts/python.exe -m app.blog.central_migration --upgrade
```

Use the selected app's key for later configured targets. There is deliberately no
all-app upgrade option. Imports, startup and normal reads never run migrations.

Blog upgrades use a separate public.blog_schema_version table; consumer apps'
existing alembic_version histories are not used or stamped. Existing untracked
blog tables or unknown revisions cause refusal. A current marker still requires
schema verification, rather than silently accepting drift.

Each PostgreSQL migration holds a transaction-scoped advisory lock, uses bounded
lock/statement timeouts and verifies columns/types/nullability, primary/unique
keys, required defaults, checks, local foreign keys and indexes before commit.
The central runner accepts only its expected parent or its own current revision;
it cannot apply unrelated pending migrations. Root Alembic env now accepts an
explicit connection for this transaction; its ordinary CLI path is unchanged.

Rollback is tested on isolated SQLite: empty blog tables can be removed and
recreated; any recorded blog data blocks destructive downgrade. Populated central
designations also block downgrade. No live downgrade was performed. SQLite tests
exclude PostgreSQL-specific JSON predicates; PostgreSQL SQL compilation and actual
live DDL/schema verification cover those declarations separately. No configured DB
was used for synthetic record insertion or regression-test data.

Checks: 44 offline module/schema tests; 22 isolated dashboard/catalog/access/model
endpoint regressions; 31 Remix admin tests; admin typecheck/build; focused Ruff and
Python compilation passed. Final post-commit verification used read-only sessions
on all four app DBs plus central schema. No blog/category/asset records, grants,
provider/B2 calls, consumer code changes, new packages or deployment were included.
Phase 3 implements the backend write rules/APIs and profile-editing validation;
Phase 5 still replaces localStorage in the editor with those APIs.


## Phase 3 - backend APIs completed locally (2026-10-24)

Implementation is contained in router.py, contracts.py, content.py, service.py,
metadata.py and http.py. The shared connector selects only server-registered app
keys. Each request verifies blog_0001; it never creates tables automatically.
Consumer application code and the editor persistence flow were not changed.

Confirmed backend .env settings (PUBLIC_BLOG_URL suffix after each BLOG_<KEY>):

| Key | Public blog base |
| --- | --- |
| AZEEMLAB | https://www.azeemlab.com/en/blog |
| AILETTERGEN | https://ailettergen.com/en/blog |
| GENZEE_VIDEO | https://www.genzee.video/en/blog |
| BONDSGAME | https://www.bondsgame.click/news |

For example BLOG_AZEEMLAB_PUBLIC_BLOG_URL supplies the canonical base. The fixed
/en/ paths permit English publication only. A confirmed {locale} template can be
configured later; the backend never guesses another locale route. Slugs append to
these bases. These are user-confirmed paths, not a deployed consumer rendering test.

All paths below start with /api/v1/dashboard/blog and require a valid approved,
active dashboard session and the blog-editor page permission:

| Method/path | Behavior |
| --- | --- |
| GET /apps | Existing safe independent app catalog |
| GET /profile | Own membership name/designation |
| PATCH /profile | Update own name/designation only |
| GET /apps/{app_key}/settings | Safe canonical configuration |
| GET /apps/{app_key}/categories | Paged categories, optional literal search |
| POST /apps/{app_key}/categories | Resolve normalized name or create category |
| GET /apps/{app_key}/posts | Paged list, optional status/locale/literal search |
| GET /apps/{app_key}/posts/{id} | Full editor JSON and server-generated fields |
| POST /apps/{app_key}/posts | Create draft or publish |
| PATCH /apps/{app_key}/posts/{id} | Update with required current version |

App paths additionally require blog.app.<key>. Create/update require blog.create
or blog.update respectively; creating a category accepts either operation grant.
Publishing, editing or unpublishing a currently published post also requires
blog.publish. Existing admin bypass semantics remain those of dashboard.require;
ordinary members require explicit grants. The catalog defines these components
without issuing grants. There is no DELETE route, including for administrators.

Create body example:

```json
{
  "title": "A useful article",
  "locale": "en",
  "category_name": "Engineering",
  "content_json": {
    "type": "doc",
    "content": [{"type":"paragraph","content":[{"type":"text","text":"Article content."}]}]
  },
  "status": "draft"
}
```

Use category_id for an existing category or category_name to resolve/create one.
PATCH accepts changed fields plus version; an outdated version returns 409.
category_id and cover_asset_id accept null to clear their selections. A published
post retains its slug and locale permanently, even after it becomes a draft or
archive. Draft title/locale changes regenerate its slug. Conflicts use bounded
unique suffix retries and database uniqueness constraints. Category creation and
post writes share one app transaction; invalid writes roll back together.

The backend copies the verified membership name/designation and safe linked user
avatar into each new post. There is no user FK in consumer databases; subsequent
profile edits do not rewrite old authors. Caller-supplied identity, slug, metadata,
HTML copies and unknown writable fields are rejected. Only bounded, allowlisted
Tiptap JSON is stored. HTTP requests are bounded to 2 MiB; documents to 1 MiB,
200,000 text characters, 8,000 nodes and depth 24. Unsafe links, arbitrary iframe
nodes, data URLs and unregistered media are rejected. HTTPS YouTube videos are
normalized to supported embed URLs without a network call.

Uploaded media nodes reference an asset_id in the selected app DB. The server
resolves and overwrites src from its stable public URL, checks MIME/node agreement
and requires an image for the cover. Public B2 uploading/asset registration is
Phase 4; these APIs neither upload nor generate signed download URLs. Public
renderers must still escape text/attributes and safely serialize JSON-LD; JSON
validation does not replace safe rendering in the later consumer integration.

Metadata is deterministic from actual title/excerpt/document, author snapshot,
locale, publication/modification times and cover. It generates canonical, Open
Graph, Twitter, robots and BlogPosting data without provider calls or invented
claims. Draft/archived records have no index permission or BlogPosting object.

Mutations check dashboard Origin before body parsing. Reads retain the existing
no-store dashboard middleware. Database errors and validation errors return safe
messages; raw SQL/credentials/bodies are not logged by this module. Profile and
its central audit commit together. App writes commit before a separate central
audit: cross-database atomicity is not claimed. audit_recorded=false explicitly
reports an audit outage while retaining successful app writes; callers must not
retry an already successful POST just to repair audit. There is no durable audit
outbox or create idempotency key in this phase. After an ambiguous timeout,
reload/list records before retrying a create.

Validation: 63 offline module/schema/content tests; 35 isolated API/catalog/
dashboard/model-endpoint tests; existing 31 admin tests and admin typecheck passed.
SQLite checks cover app isolation, grants, publish separation, JSON rejection,
media resolution, category rollback, collision handling, optimistic versions,
author snapshots, canonical generation, Origin/body limits and audit outages.
They do not prove PostgreSQL concurrent writes, production rendering, B2 upload,
real configured-database write grants or deployment acceptance. No synthetic
records or permission changes were made in configured databases. No packages,
consumer app changes, provider calls or deployment were included.

Reproduce from the backend root:

```powershell
uv run python -m pytest --noconftest app/blog/tests -q
uv run python -m pytest tests/test_blog_api.py tests/test_blog_catalog.py tests/test_dashboard.py tests/test_model_endpoints.py -q
uv run ruff check app/blog app/services/dashboard.py tests/test_blog_api.py
```

Next: Phase 4 implements public B2 media uploads; Phase 5 connects the editor UI
to these APIs and replaces localStorage persistence. Phase 6 verifies the full
configured flow and consumer rendering separately.


## Phase 4 - public B2 upload backend (2026-10-24)

The admin gateway performs uploads; consumer apps need no upload endpoints or
code changes. Separate BLOG_B2 settings never reuse the existing audio/log bucket
or credentials. The configured bucket is blogPost001 and generated object keys
are blogs/<app_key>/<UUID>.<verified-extension>. Each asset row is saved in the
selected app database. No media bytes or credentials go into the blog JSON.

Fill these fields in **D:/AIModels_SearchAPIS/.env**, beside the existing BLOG_*
settings. The empty entries and bucket name have already been added. They belong
to the Python gateway, not admin/.env or any consumer app environment:

```dotenv
BLOG_B2_BUCKET_NAME=blogPost001
BLOG_B2_APPLICATION_KEY_ID=
BLOG_B2_APPLICATION_KEY=
BLOG_B2_ENDPOINT_URL=
BLOG_B2_PUBLIC_BASE_URL=
```

Application Key ID goes in APPLICATION_KEY_ID; the secret application key goes in
APPLICATION_KEY. B2's key name is a human label and is not needed by the SDK.
Use a dedicated key restricted to this bucket with upload/read/cleanup access.
Never paste keys into chat, public files or browser settings. ENDPOINT_URL is the
bucket's HTTPS S3 endpoint shown by B2, such as
https://s3.<region>.backblazeb2.com. PUBLIC_BASE_URL is the actual stable public
bucket/CDN root before blogs/, without query strings. For a native public download
URL ending /file/blogPost001, include that entire prefix. Do not use the S3 API
endpoint alone as the public bucket URL. The public URL must serve the object
without authentication. No signed download URL generation is used. Existing
shared B2 settings remain unchanged. Public media is readable even before publish.

Restart/reload the gateway after setting deployment environment variables. The
upload endpoint fails closed with a safe 503 while any setting is missing. Local
.env fallback is read on each request; explicit process variables take priority.

API contract:

- POST /api/v1/dashboard/blog/apps/{app_key}/assets?filename=photo.png
- Send raw file bytes; Content-Type is the exact allowed MIME, not multipart.
- Send a fresh UUID in Idempotency-Key for each file. Preserve it on retry.
- Require the existing dashboard session, valid Origin, page blog-editor,
  app blog.app.<key>, and either blog.create or blog.update permission.
- Returns asset including id/object_key/public_url/media_type/size/dimensions/
  duration, reused, and audit_recorded on new uploads. Both new/reused success
  currently return HTTP 201. Reuse verifies B2 SHA-256 metadata; another file with
  the same token/format is rejected. Reuse still requires sending the same bytes.
- GET the same /assets path for the selected app's paginated media library.
- Editor nodes use asset_id, and cover_asset_id references an image asset. Post
  APIs independently resolve URLs and type-check against the same selected DB.

Limits: PNG/JPEG/WebP 10 MiB; MP3/WAV/Ogg 25 MiB; MP4/WebM 64 MiB; maximum
40 million image/video pixels; audio/video duration up to four hours. Other file
types, including SVG, HTML, executables and arbitrary attachments, are rejected.
Signature, supported codec/container, dimensions and duration checks run locally
with ffprobe before B2 I/O. ffprobe is already available locally and included with
the existing Docker ffmpeg installation. Missing ffprobe fails closed. Probe
checks do not claim antivirus scanning or complete decoding/transcoding. Local
protocol/container restrictions prevent playlist/network ingestion. No remote
URL fetcher or automatic media transformation is exposed.

The request streams to a temporary file with actual byte counting, including
chunked requests. Temporary files are removed on every exit. Two upload slots per
process bound work; full capacity returns 429. Receive/probe/request/I/O deadlines
are bounded. This is not a distributed quota or a proxy request-limit setting.
Public immutable cache headers use generated filenames; user filenames do not
control paths or response headers. B2 automatic SDK retries are disabled.

PostgreSQL uses a transaction-scoped advisory lock on the app/object key to
serialize retries across workers before B2 writes. The assets table's existing
unique object_key remains authoritative; no migration or central asset table was
added. A definite insert failure compensates by deleting the uploaded object
version when B2 supplies its version ID. Failed/ambiguous uploads attempt cleanup;
failures log a server-generated object key without provider error text. If commit
is uncertain, the object is deliberately retained. Retry the same token to find
an already committed row or finish saving the unregistered object. There is no
cross-system DB/B2 transaction. Central audit uses the Phase 3 audit_recorded
contract and cannot erase a successful app write.

Explicit orphan reconciliation (gateway root, operator-only):

```powershell
uv run python -m app.blog.orphans --app azeemlab --object-key blogs/azeemlab/<UUID>.png
# After inspecting dry-run status, explicitly request orphan deletion:
uv run python -m app.blog.orphans --app azeemlab --object-key blogs/azeemlab/<UUID>.png --delete
```

Use an exact cleanup-log key. The command locks that key, preserves every
registered asset regardless of post usage, preserves objects younger than 24
hours, and defaults to inspection only. Deletion removes only a stale unregistered
object/version. There is no scheduled bucket-wide sweep, no API deletion of
registered assets, and no deletion merely because an editor has not attached an
upload yet. B2 version/lifecycle retention for older orphan versions remains a
bucket-operator setting; this command does not scan historical versions. Dry-run
HEAD and PostgreSQL connection checks require real credentials; they were not run.

Validation: 13 media regressions plus the existing 35 API/catalog/dashboard/model
checks (48 total), and 63 offline module/schema tests pass. Media tests use fake
B2 and disposable SQLite, covering app isolation, same-token reuse/content hash,
Origin/permission rejection, declared/streamed limits, invalid files, definite DB
failure compensation, uncertain commits, and protected orphan reconciliation.
A generated WAV and malformed PNG also exercise the actual local ffprobe binary.
Ruff and Python compilation pass. No live B2 upload/delete, configured DB writes,
PostgreSQL concurrent upload acceptance, new package, consumer code change or
deployment was performed. Credentials/public base and live acceptance remain
required before calling storage production-ready. Phase 5 wires the editor UI.


### Subsequent B2 acceptance smoke and Phase 5 handoff (2026-10-24)

After dedicated credentials were supplied, authenticated bucket listing returned
200 for blogPost001. A separately authorized tiny generated WAV upload returned
200; its configured public URL returned 200 with byte-for-byte matching content;
cleanup of that exact test object/version returned 204. No blog asset row or real
post was inserted for this smoke. This verifies the tested upload/public-download/
cleanup path, not every media format, concurrent writes or deployment.

The active Next.js editor at D:/voice_generator_free/admin_dashboard is now wired
to these APIs (Phase 5 local implementation). See that checkout's README for the
server proxy, safe credential boundary, tests and remaining Phase 6 acceptance.


## Phase 6 local acceptance completed (2026-10-24)

The four configured app DBs and public B2 passed the scoped live checks after
recovery and integration fixes. All six labelled test posts are archived; eight
registered media fixtures are retained; all four temporary sessions are revoked.
See [PHASE6_ACCEPTANCE.md](PHASE6_ACCEPTANCE.md) for evidence, exact coverage,
corrections and exclusions, and phase6-verification.json for the 70 recorded checks.
Deployment is deferred at the user's explicit request. The two blank app DBs and
consumer application rendering remain outside this completed local acceptance.

## Official media schema (2026-10-25)

The active `/blog-editor` now registers official `@tiptap/extension-image`,
`@tiptap/extension-audio`, and `@tiptap/extension-youtube` (matching core 3.32.0),
plus the existing StarterKit and official TableKit. It does not register custom
nodes or add custom attributes to official nodes. Image JSON uses `image` with
`src`, `alt`, `title`, `width`, `height`; audio uses official `audio` attributes;
YouTube uses `youtube` with `src`, `start`, `width`, `height`.

Uploaded video and generic attachments use an ordinary paragraph/text/Link mark.
There is no invented `video`/`file` node or substitute community extension.
Captions from legacy records become ordinary paragraphs. The existing picker,
upload retry token, app authorization, profile snapshots and save controls remain.
Only image/audio assets are embedded by the active editor; uploaded videos are
links in the editor. AzeemLab enhances a standalone registered video link into a
native player on its public article page.

Official nodes intentionally contain no `asset_id`. The backend resolves image
and audio `src` values by exact `blog_assets.public_url` in the selected app's
database and verifies MIME, rather than trusting an arbitrary source. The public
reader batches URL lookups, checks its configured media origins and renders only
resolved app-local image/audio/video assets. External images pasted into JSON can
be recognized as standard nodes but cannot be saved unless registered in that
app's asset database. B2 uploads and credentials remain backend-owned.

Existing database posts are never mass-migrated or rewritten on load. Legacy
custom media JSON is converted in memory before the active editor loads it; an
explicit Save writes the standard representation. Backend/consumer read support
for legacy records remains for backward compatibility. The separate legacy
`BlogEditorView` demo and its existing browser drafts are retained; it is not the
active database editor and its old extensions are not imported by that editor.

Deploy/restart the backend and editor together, followed by the AzeemLab consumer.
Other consumer projects discussed earlier are not integrated by this change.
No schema migration, database write, test, type/lint check, build or browser
check was run. Existing import-contract test sources were updated but not run.

References:
- https://tiptap.dev/docs/editor/extensions/nodes/image
- https://tiptap.dev/docs/editor/extensions/nodes/audio
- https://tiptap.dev/docs/editor/extensions/nodes/youtube


## Author biography snapshot (2026-10-26)

Revision `blog_0003` adds nullable `blog_posts.author_description` (TEXT).
Run the existing operator CLI separately for each configured app:
`python -m app.blog.migrate --app <app_key> --action upgrade`.
Never migrate at startup or on requests. PostgreSQL appends the physical column;
placing it after designation would require a table rebuild.

New posts snapshot the verified membership description using the existing
2,000-character plain-text validator. On an explicit post Save, author name,
designation, avatar and description refresh from the original creator's current
profile, even when an administrator edits the post. Creator identity comes from
server-owned `metadata_json.created_by_membership_id`, or for legacy posts from
one unambiguous successful creation audit with the exact app/post target.
Missing, deleted or ambiguous creator identities preserve the saved author;
the current editor is never used as a substitute. Profile edits alone do not
rewrite existing articles. Metadata also exposes `author_description`.

The AzeemLab reader selects `author_description` directly, with a legacy metadata
fallback, and no longer substitutes a static team biography by matching names.
After deploying/restarting the backend and consumer, open each affected article
and Save changes once to refresh its author details. No automatic bulk backfill
or new migration is part of this correction; existing `blog_0003` is required.
