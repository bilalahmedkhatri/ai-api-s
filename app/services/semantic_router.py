"""Semantic intent router using cosine similarity against pre-defined centroids.

Uses numpy (already installed via torch/whisper) for vector math.
Centroids are computed lazily on first call and cached for the process lifetime.

Categories → recommended model mapping is advisory; Dispatcher makes final call.
"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ── Intent definitions ─────────────────────────────────────────────────────────
# Each entry: (category_name, sample_sentences, recommended_model_hint)
_INTENT_SAMPLES: list[tuple[str, list[str], str]] = [
    (
        "coding",
        [
            "write a Python function to sort a list",
            "debug this JavaScript error",
            "explain how async await works",
            "implement binary search in Rust",
            "what does this SQL query do",
            "fix the type error in my TypeScript code",
        ],
        "qwen/qwen-2.5-coder-32b-instruct",
    ),
    (
        "creative_writing",
        [
            "write a short story about a dragon",
            "compose a poem about the ocean",
            "create a dialogue between two characters",
            "write a product description for a coffee mug",
            "give me a catchy slogan for my startup",
        ],
        "gpt-4o",
    ),
    (
        "mathematics",
        [
            "solve this differential equation",
            "prove that root 2 is irrational",
            "what is the derivative of x squared",
            "calculate the area under the curve",
            "simplify this algebraic expression",
        ],
        "gpt-4o",
    ),
    (
        "general_knowledge",
        [
            "what is the capital of France",
            "who invented the telephone",
            "explain the theory of relativity",
            "what causes thunder",
            "when did World War II end",
        ],
        "gpt-4o-mini",
    ),
    (
        "general",
        [
            "help me with something",
            "I have a question",
            "can you assist me",
            "what do you think about this",
            "tell me more",
        ],
        "gpt-4o-mini",
    ),
]

_SIMILARITY_THRESHOLD = 0.75  # below this → fallback to "general"


@dataclass
class SemanticResult:
    category: str
    score: float
    model_hint: str


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity — pure numpy, no scipy."""
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


async def _build_centroids() -> list[tuple[str, np.ndarray, str]]:
    """Embed all sample sentences and average them into category centroids."""
    from app.services.embedding import embed  # lazy — avoids circular imports

    all_sentences: list[str] = []
    boundaries: list[int] = [0]
    for _, samples, _ in _INTENT_SAMPLES:
        all_sentences.extend(samples)
        boundaries.append(len(all_sentences))

    vectors = await embed(all_sentences)  # single batched API call
    np_vecs = np.array(vectors, dtype=np.float32)

    centroids: list[tuple[str, np.ndarray, str]] = []
    for i, (name, _, model_hint) in enumerate(_INTENT_SAMPLES):
        chunk = np_vecs[boundaries[i] : boundaries[i + 1]]
        centroid = chunk.mean(axis=0)
        centroids.append((name, centroid, model_hint))
        logger.debug("Built centroid for '%s' from %d samples", name, len(chunk))

    return centroids


# Module-level cache; populated on first classify() call.
_centroids: list[tuple[str, np.ndarray, str]] | None = None


async def classify(query: str) -> SemanticResult:
    """
    Embed the query and return the closest intent category.
    Falls back to "general" if no centroid exceeds the similarity threshold.
    """
    global _centroids
    if _centroids is None:
        logger.info("Building intent centroids (first call — embedding sample sentences)")
        _centroids = await _build_centroids()

    from app.services.embedding import embed  # noqa: PLC0415

    query_vec = np.array((await embed([query]))[0], dtype=np.float32)

    best_name, best_score, best_model = "general", 0.0, "gpt-4o-mini"
    for name, centroid, model_hint in _centroids:
        score = _cosine(query_vec, centroid)
        logger.debug("Similarity to '%s': %.4f", name, score)
        if score > best_score:
            best_name, best_score, best_model = name, score, model_hint

    if best_score < _SIMILARITY_THRESHOLD:
        best_name, best_model = "general", "gpt-4o-mini"

    logger.info("Semantic route: category=%s score=%.4f model=%s", best_name, best_score, best_model)
    return SemanticResult(category=best_name, score=best_score, model_hint=best_model)
