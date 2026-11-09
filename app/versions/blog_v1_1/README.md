# Blog API V1.1

Backend only. Existing `/api/v1/dashboard/blog` handlers and schema marker remain
unchanged. V1.1 uses the same server under `/api/v1.1/dashboard/blog`.

## Migration and compatibility

V1.1 requires the additive `blog_posts.blog_details_json` column: PostgreSQL JSONB,
non-null, default `{}`, with an object-type check. Existing rows retain all their
data and receive the empty default. The existing `blog_0003` marker is deliberately
preserved because V1 checks that exact marker on each request.

No migration runs at startup or in request handlers. These commands are operator
actions, one selected app at a time:

```powershell
python -m app.versions.blog_v1_1.migrate --sql
python -m app.versions.blog_v1_1.migrate --app <app_key> --action upgrade
python -m app.versions.blog_v1_1.migrate --app <app_key> --action verify
```

`<app_key>` may be any configured blog database in `APP_KEYS` (for example
`genzee_video`, `azeemlab`, `ailettergen`, `bondsgame`, `sikanderart`, or
`e_sahulat`). Run upgrade and verify separately for each selected database;
there is intentionally no implicit all-database write.

The upgrade is transactional on PostgreSQL, bounded by lock/statement timeouts,
and idempotent. It requires an existing `blog_0003` database. There is no automatic
downgrade or data deletion. Use this extension's verifier after upgrading: the
old base verifier intentionally expects the old exact column inventory and will
report the additional column. Do not run the old base upgrade/verify CLI against
an extended database as a way to repair that inventory warning.

V1 reads/writes continue using their existing projections and do not manage the
new details column. V1.1 detail reads and writes expose `blog_details_json`;
list responses continue excluding article bodies and the new JSON column.

## Create/update contract

Keep `title`, `excerpt`, `category_id` or `category_name`, `locale`, `status`, and
`content_json` as existing canonical fields. Do not duplicate them in JSON.
Send optional `blog_details` on POST/PATCH:

```json
{
  "title": "Example blog",
  "excerpt": "Summary",
  "category_name": "Technology",
  "locale": "en",
  "status": "draft",
  "content_json": {"type": "doc", "content": []},
  "blog_details": {
    "featured_image": {
      "asset_id": 12,
      "alt_text": "Description",
      "attribution": {
        "credit": "Creator",
        "sourceUrl": "https://example.test/photo",
        "license": "CC BY 4.0",
        "licenseUrl": "https://example.test/license"
      }
    },
    "content_images": []
  }
}
```

PATCH also requires the current `version`. Omitted details preserve matching
saved details; removing document images removes their stale details. An explicit
details object must describe exactly the document's image assets. At most three
image nodes are allowed, including repeated images. The same image asset may be
featured and inline. Cover asset IDs must agree if both old/new fields are sent;
do not send legacy `cover_image` together with `blog_details`.

Only registered uploaded assets in the selected app are accepted. Operators use
their own uploads; admins can use selected-app assets. One batch query validates
all media references and MIME types. URLs are server-resolved. Content alt text is
copied into Tiptap image nodes. Credits/licences are optional declarations, not
automatic verification of usage rights. The server adds `author_image` to stored
details from the verified author snapshot; clients cannot supply it.

Existing image-bearing posts get safe image details on their next V1.1 write;
unknown credits/licences remain blank. Invalid/old profile avatars must first be
uploaded/saved through V1.1. Old signed-upload URLs without the author-name format
need reupload. Historical image objects are not renamed.

Publish date remains server-owned and is set on actual publication by the existing
rules. No scheduling worker, scheduling contract or user-supplied `published_at`
is introduced here. A draft remains a draft until a permitted publish request.

## Upload URLs

Image upload remains separate from post save:

```text
POST /api/v1.1/dashboard/blog/apps/{app_key}/assets?filename=photo.png&title=Blog%20Title
Content-Type: image/png
Idempotency-Key: <UUID>
```

The server derives the actual key `blogs/{app_key}/blog-title-<UUID>.png`,
stored filename, validated extension, and UTF-8 content disposition. Use the same
title, bytes and upload token on retry. A token bound to a different title/file
returns 409. Titles are normalized, bounded, URL-encoded as needed; changing a
post title never renames existing immutable image objects. Existing media size,
signature, decode/probe, advisory lock, retry hash and cleanup protections remain.

Profile image upload `POST /profile/avatar` uses
`blogs/authors/{membership_id}/author-name-<content-hash>.png` and returns
`{url, upload_token}`. Save through `PATCH /profile` with `avatar_url` and
`avatar_upload_token`. The signed receipt is member/URL-bound and valid for one
day. External URLs, changed URLs and another member's receipt are rejected.
Clearing the avatar remains supported. No remote image URLs are downloaded.

## Security and timing

The existing session dependency is cached per request by FastAPI. V1.1 loads
operator grants once, checks page/app/operation/publish grants in memory, and
does not cache access across requests. Current revocation takes effect on the
next request. Update ownership uses the exact post; legacy ownership checks use
only that post's unambiguous creation audit. Stale versions fail before author
refresh, asset processing or writes. Conditional updates still prevent races.

Responses under the V1.1 blog prefix, including errors, use `Cache-Control:
no-store` and `Server-Timing: backend;dur=<milliseconds>`. This measures time to
response headers through this ASGI middleware. It excludes frontend, network
transfer, proxy delays and complete body transfer. No timing query or extra auth
request is added. Audit records remain synchronous and committed after the post;
an audit failure is reported without pretending the successful post rolled back.

The 0–3 second goal is for the complete user request. Local SQLite/mock timings
cannot establish that goal for hosted databases/storage or uploads. Measure real
backend p50/p95 with representative bodies/concurrency and hosting before making
an end-to-end latency claim. Do not remove validation or add blind write retries
to meet a timing target.

## Local verification (2026-10-31)

18 V1.1 tests and 60 existing blog regression tests passed; focused Ruff and diff
checks passed. V1.1 tests exercise separate-column persistence, V1 compatibility,
0/1/3 content images, over-limit/external/cross-app/other-owner image rejection,
upload retry/title binding, signed profile receipts, stale versions, revoked
grants, migration idempotence and preservation of existing rows.

An operator publishing a post used one session lookup in V1.1 and one permissions
SELECT, versus four permissions SELECTs through V1. Ten sequential, warmed-up
samples per operation with a small text document and disposable SQLite yielded:

| Version | Create p50 / p95 (ms) | Update p50 / p95 (ms) |
| --- | --- | --- |
| V1 | 14.16 / 19.05 | 12.48 / 19.24 |
| V1.1 | 9.21 / 11.57 | 9.27 / 15.28 |

With only ten samples, p95 is the maximum observed sample (nearest rank). These
are in-process HTTP/SQLite measurements, not provider/B2, PostgreSQL, concurrent
load, deployed latency or frontend acceptance. Live migration and deployment
were not performed. Windows asyncio socket-pair creation required running the
isolated tests outside the sandbox.
