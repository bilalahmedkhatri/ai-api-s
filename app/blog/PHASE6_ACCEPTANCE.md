# Phase 6 local acceptance - 2026-10-24

Status: PASSED for the four configured apps. The user explicitly selected local
acceptance; deployment/hosted verification is deferred. Machine-readable evidence:
[phase6-verification.json](phase6-verification.json). All 70 recorded acceptance
checks pass after recovery and fixes. This is not a load/stress test or a claim of
consumer-site integration or production deployment.

## Configured data verification

| App | Result | Total posts | Categories | Assets |
| --- | --- | ---: | ---: | ---: |
| azeemlab | passed | 3 | 1 | 3 |
| ailettergen | passed | 1 | 1 | 1 |
| bondsgame | passed | 1 | 1 | 1 |
| sikanderart | unconfigured_skipped | - | - | - |
| e_sahulat | unconfigured_skipped | - | - | - |
| genzee_video | passed | 1 | 3 | 3 |

The six deliberately labelled test posts are archived; no test post was published.
The eight tiny registered B2 fixtures remain retained for their referenced/registered
asset records. No deletion route was added and no registered media was swept.
These table counts were zero before the acceptance run; identity sequence gaps
from collision handling are expected. Sikander Art and E Sahulat have no configured
DB URLs and were skipped, not reported as tested.

## What was exercised

- Actual per-app PostgreSQL post/category/asset writes and JSON reloads, canonical
  metadata from the confirmed URLs, author name/designation snapshots, app-local
  references and table isolation.
- Two simultaneous updates of the same PostgreSQL version: exactly one succeeds,
  the stale request returns 409. Concurrent slug collisions produce distinct slugs.
- Concurrent same-name category creation returns one 201 and one 200 with the same
  ID on a fresh-name replay. The earlier extra-check result was inconclusive;
  sanitized fresh-replay statuses/IDs are retained in phase6-extra-verification.json.
- Actual public B2 PNG/WAV/MP4 upload and byte-for-byte public download; same-token
  retry reuses the asset. Concurrent same-token uploads produce one asset/one reuse.
- Cross-app invalid media reference returns 422 and its new category rolls back.
  Missing sessions, foreign mutation Origin and administrator DELETE are denied.
- Real Next.js browser -> authenticated server proxy -> FastAPI -> configured DB/B2:
  image upload/save, archived status preservation, reload, app switching and native
  media decoding. Browser decoded a 1px PNG, 0.1s WAV and 0.4s MP4 without errors.
- Media insertion retained the existing audio/image nodes; the temporary unsupported
  playback test embed was undone and not saved. Player playback itself is not part
  of the B2 acceptance claim. At 390px, the settled document was 378px wide with no
  horizontal overflow. The immediate resize transient was not treated as a layout bug.

## Corrections found during acceptance

1. The actual backend post/category list contract uses `items`. The editor had
   expected `posts`/`categories`; corrected schemas now consume the real contract.
   A new fixture exported from isolated FastAPI requests and a frontend regression
   prevent the mock backend from concealing this mismatch again.
2. Saving an archived post now preserves its status instead of silently drafting it.
3. Inserting media after a selected node/range now preserves the selected content.
   A shared Tiptap helper is checked with a real ProseMirror transaction regression
   and the live browser replay. The test document's removed audio was restored.
4. YouTube iframe output uses an explicit origin-only referrer policy, following
   [Google's embedded-player guidance](https://developers.google.com/youtube/terms/required-minimum-functionality#api-client-identity-and-credentials).
   This corrects the observed missing-referrer configuration; successful playback
   of arbitrary third-party videos is not claimed.

## Safety, interruptions and scope

The first run stopped on a Windows/PostgreSQL connection interruption. Previously
successful test posts were archived, and its exact temporary session was revoked
on recovery. `phase6-first-run.json` preserves that failed attempt. Only GenZee was
resumed, avoiding extra posts in the already-passed apps. This does not establish
a permanent DNS/network fix. The operator preview's stdin/closed-logger issues were
corrected using a bounded stop marker and `log_config=None`; production logging code
was not changed. All four exact test sessions (21, 22, 23, 24) were read back as
revoked. No users, approvals, roles, grants, provider keys or billing data changed.
The approved existing admin supplied the author snapshot; no fake Google identity
or separately provisioned author was introduced.

Limited-user grant denial, invalid content/size, storage outages and ambiguous
commit behavior were exercised in isolated regression databases/fake B2. Existing
member grants were not changed and live limited-user provisioning was not done.
The live checks used an operator-issued short-lived session, not a new Google
consent/login test. No automatic legacy draft import/deletion was performed.
Deployment, consumer rendering/sitemaps/cache integration, Google login acceptance,
large-scale PostgreSQL load and every supported codec remain outside this scoped
local acceptance result. JPEG/WebP/MP3/Ogg/WebM were not additional live uploads.

## Validation and artifacts

- 50 isolated backend API/catalog/dashboard/model/contract checks.
- 63 offline blog module/schema/content checks.
- 43 Next.js frontend regressions, typecheck, focused ESLint and production build.
- Browser receipt: admin/output/phase6/browser-verification.json.
- Screenshot: admin/output/playwright/blog-phase6-live.png.
- Actual backend contract fixture: admin_dashboard/tests/blog-backend-contract.json.
- Temporary browser/preview servers were stopped and temporary sessions revoked.

Acceptance drivers are explicit operator tools under app/blog/acceptance*.py and
require --run-live. They are never run by imports, normal requests or regression
suites. Their loopback-only session bridge exists only while --keep-preview is
explicitly active, and does not add a route to the normal deployed application.
