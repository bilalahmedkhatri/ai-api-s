# Inference relocation and gateway dependency cleanup

## Current behavior

Original Whisper ingestion, local Kokoro inference/assets, OmniVoice experiments and
provider-specific scripts live in `D:/voice_generator_free/falai/{whisper,kokoro,omnivoice}`.
Those directories are separate worker source units, not deployed fal.ai applications.
Do not import sibling files from the gateway; workers need separate environments and
an authenticated transport adapter before they can be used.

- `POST /api/v1/ingest/audio/` and `POST /api/v1/ingest/audio/url` are removed:
  requests return 404 and neither route appears in OpenAPI.
- Local Kokoro is excluded from public model/voice lists and rejected before new
  TTS jobs, samples (including cached samples), manual retries and automatic recovery.
- Gemini and remote Kokoro remain supported. Existing B2 audio, job status,
  streaming and signed-URL renewal remain available through their authorized flows.
- DB catalog/history rows, job snapshots, grants and credentials were not rewritten.
  Local jobs cannot resume until a supported worker transport exists. Shared schemas,
  seeding/catalog metadata, checkpoint timing, ownership and B2 remain gateway-owned.
- Do not enable local fallback here; legacy configuration fields do not load models.

## Dependency cleanup

Removed `openai-whisper`, `kokoro-onnx` and `gradio-client` from `pyproject.toml`,
regenerated `uv.lock` offline and synced the gateway environment. The lockfile
pruned 36 package entries without changing retained package versions. NumPy is now
explicit (`numpy>=2.4.6`); SoundFile, LiteLLM, NLTK and B2 SDKs are retained.

```bash
uv sync --locked --all-groups
uv lock --check --offline
uv run --locked pytest tests/ -q -p no:cacheprovider
```

## Verification (2026-10-18)

333 isolated backend tests passed after dependency cleanup. Gateway/retained-library
imports and offline lockfile consistency passed. External providers/storage were
mocked; no real inference, configured DB migration, hosted Linux RAM measurement,
Git push or deployment was performed.

The current Dockerfile/CI still target Python 3.12, while the project requires 3.14;
those deployment paths were not validated or changed in this cleanup.

See the worker directory README, VERIFICATION.md and relocation-manifest.json for
the source move inventory. Manifest dependency hashes describe the original
pre-cleanup snapshot and are intentionally different from current dependency files.
