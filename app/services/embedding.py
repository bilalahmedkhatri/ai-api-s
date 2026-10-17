"""Embedding service — thin wrapper around litellm.aembedding().

Uses litellm so any provider (OpenAI text-embedding-3-small, Cohere, etc.)
can be swapped via .env without touching this file.

Ponytail note: 3 local sentence-transformer models were spec'd but rejected
(Step 1 — YAGNI: no semantic router exists yet; Step 5 — adds GB of downloads).
litellm is already installed and handles embeddings via API.
"""

import logging
import time
from functools import lru_cache

import litellm

from app.services.query_telemetry import record_call

logger = logging.getLogger(__name__)

# Default model — override with EMBEDDING_MODEL in .env
_DEFAULT_MODEL = "text-embedding-3-small"


@lru_cache(maxsize=1)
def _model() -> str:
    """Read once from settings; cached for the process lifetime."""
    try:
        from app.core.config import settings  # noqa: PLC0415
        return getattr(settings, "embedding_model", _DEFAULT_MODEL)
    except Exception:
        return _DEFAULT_MODEL


async def embed(texts: list[str]) -> list[list[float]]:
    """Return a list of embedding vectors for the given texts.

    Args:
        texts: Non-empty list of strings to embed.

    Returns:
        Parallel list of float vectors, one per input string.
    """
    if not texts:
        return []

    model = _model()
    logger.debug("Embedding %d text(s) via %s", len(texts), model)

    started = time.monotonic()
    try:
        response = await litellm.aembedding(model=model, input=texts, timeout=15, num_retries=0)
    except Exception as exc:
        record_call("embedding", model, started, error=exc)
        raise
    record_call("embedding", model, started, response=response)
    return [item["embedding"] for item in response.data]
