# Blog media uploader ownership - 2026-10-25

The upload popup now has distinct Choose a file / Upload & insert controls, selected-file name/size/preview, pointer and disabled cursors, and the existing shared loading spinner with upload-button contrast. The My images tab shows a paginated thumbnail gallery and inserts a selected image without reuploading it. Audio/video uploads remain supported.

`BlogMediaLibrary.tsx` uses the existing authenticated Next proxy. GET assets accepts `media_type=image`; listing and total count always filter by the verified current membership in the selected app DB. There is no administrator bypass and no caller-supplied owner parameter. Uploads record `uploaded_by_membership_id` from the verified session. Retry tokens cannot return/reuse another uploader's asset or an unassigned legacy asset.

The additive `blog_0002` migration adds a nullable uploader column and an uploader/id index. Frozen schema_v1 and b001 are unchanged. There is no cross-database FK and author snapshots remain separate. Existing posts/assets are retained; legacy owners remain NULL and are excluded from My images. Existing post media references continue to resolve normally; this change does not privatize public B2 objects or remove post/media features.

Configured targets were individually upgraded using the explicit migrate CLI. See `admin/output/media-ownership-verification.json` for fresh post-commit read-only results. Sikander Art and E Sahulat remain unconfigured; do not migrate them until configured explicitly. New imports/startup/request paths never migrate automatically.

BondsGame's previously reproduced SQLSTATE 42P05 collision is addressed in the normal connector with SQLAlchemy's documented UUID-based prepared_statement_name_func plus the existing NullPool. Fresh-connection checks are limited local evidence, not PostgreSQL load/deployment acceptance. Pooler lifecycle/configuration remains an operator concern.

Validation: 73 isolated offline/schema checks, 52 isolated backend checks including two authenticated administrator identities, and 52 frontend regressions passed. Typecheck, focused ESLint/Ruff and production build passed. Fake B2 was used in upload regressions; no live file was uploaded and no browser verification or deployment was performed.

Manual acceptance after restarting the backend/admin:
1. Open Upload media: Choose a file must be distinct from Upload & insert; uploading remains disabled until selection.
2. Select an image, confirm name/size/preview, then upload. The spinner must remain visible and duplicate actions/closing must be disabled while pending.
3. Reopen My images and insert that image into the editor without reuploading it.
4. Use a second approved user in the same app: the first user's image must not appear. Check the reverse direction, pagination and an app switch too.
5. Verify the empty state for an account with no attributed images and confirm that a saved post's older media still displays.
6. Smoke-check audio/video upload and editor save/reload. Existing unattributed images are intentionally absent from the gallery; ownership is never guessed.

B2 remains public: uploader filtering controls dashboard discovery; a person with a public file URL can still retrieve it.
