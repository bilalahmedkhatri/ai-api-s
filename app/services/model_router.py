"""Model routing matrix and LiteLLM orchestration for 30B+ models.

Intent → model selection:
  coding / mathematics  → Qwen-2.5-Coder (primary) → Llama-3.3-70B (fallback)
  general_knowledge/RAG → Llama-3.3-70B (primary) → Command-R-Plus (fallback)
  creative_writing      → Llama-3.3-70B (primary) → Llama-3.1-8B (fallback)
  general (fallback)    → Llama-3.3-70B (primary) → Llama-3.1-8B (fallback)

All models accessed via API providers (Groq / OpenRouter).
No local weights downloaded — purely API-based routing.

Ponytail note:
  - tenacity was spec'd but rejected (Step 5): litellm has built-in
    fallbacks= and timeout= params that cover the same behaviour.
  - Model roster is documented in MODELS.md for human review; no code
    implements "automated HF leaderboard scanning" (Step 1 — YAGNI).
"""

import logging
import time
from dataclasses import dataclass

import litellm

logger = logging.getLogger(__name__)

# ── Model roster ───────────────────────────────────────────────────────────────
# Format: provider/model-name as understood by litellm.
# Override individual entries via ROUTING_* env vars (see config.py).

_QWEN_72B           = "groq/qwen-2.5-coder-32b"          # math + code primary
_LLAMA_70B          = "groq/llama-3.3-70b-versatile"     # reasoning + creative primary
_COMMAND_R          = "cohere/command-r-plus"             # RAG primary
_OPENROUTER_FALLBACK = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
_LLAMA_8B           = "groq/llama-3.1-8b-instant"        # cheap fast fallback

_PRIMARY_VISION = "gemini/gemini-flash-latest"
_VISION_FALLBACKS = [
    "openrouter/google/gemma-4-31b-it:free",
    "openrouter/free"
]

# ── Routing matrix ─────────────────────────────────────────────────────────────
# key: intent_category (from semantic_router.py)
# value: (primary_model, [fallback_models])
ROUTING_MATRIX: dict[str, tuple[str, list[str]]] = {
    "coding":           (_QWEN_72B,      [_LLAMA_70B, _LLAMA_8B]),
    "mathematics":      (_QWEN_72B,      [_LLAMA_70B, _LLAMA_8B]),
    "creative_writing": (_LLAMA_70B,     [_LLAMA_8B, _COMMAND_R]),
    "general_knowledge":(_LLAMA_70B,     [_COMMAND_R, _LLAMA_8B]),
    "general":          (_LLAMA_70B,     [_LLAMA_8B, _OPENROUTER_FALLBACK]),
    # Rule-engine categories that still need LLM
    "math_task":        (_QWEN_72B,      [_LLAMA_70B]),
    "math_expression":  (_QWEN_72B,      [_LLAMA_70B]),
}
_DEFAULT_PRIMARY  = _LLAMA_70B
_DEFAULT_FALLBACKS = [_LLAMA_8B, _OPENROUTER_FALLBACK]

LLM_TIMEOUT = 15  # seconds — hard deadline before fallback triggers


@dataclass
class LLMResult:
    answer: str
    model_used: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    elapsed_ms: float


def _pick_models(intent: str) -> tuple[str, list[str]]:
    """Return (primary, fallbacks) for the given intent category."""
    return ROUTING_MATRIX.get(intent, (_DEFAULT_PRIMARY, _DEFAULT_FALLBACKS))


async def call_llm(
    prompt: str,
    intent: str,
    *,
    force_model: str | None = None,
    max_tokens: int = 1024,
) -> LLMResult:
    """
    Call the appropriate 30B+ model for the intent, with automatic fallbacks.

    Args:
        prompt:      The full prompt string (query + optional RAG context).
        intent:      Intent category from semantic_router or rule_engine.
        force_model: Override routing matrix (e.g., from settings.default_llm_model).
        max_tokens:  Maximum response tokens.

    Returns:
        LLMResult with answer, model name, token counts, and USD cost.
    """
    t0 = time.monotonic()
    primary, fallbacks = _pick_models(intent)

    if force_model:
        primary = force_model
        fallbacks = list(ROUTING_MATRIX.get(intent, (None, fallbacks))[1])

    messages = [{"role": "user", "content": prompt}]

    try:
        resp = litellm.completion(
            model=primary,
            messages=messages,
            max_tokens=max_tokens,
            timeout=LLM_TIMEOUT,
            fallbacks=[
                {"model": m, "messages": messages, "max_tokens": max_tokens}
                for m in fallbacks
            ],
            # litellm will retry fallbacks automatically on rate-limit / timeout.
        )
    except Exception as exc:
        logger.error("All LLM models failed for intent=%s: %s", intent, exc, exc_info=True)
        raise

    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    answer = resp.choices[0].message.content.strip()

    # ── Token counting & cost ──────────────────────────────────────────────────
    usage = getattr(resp, "usage", None)
    prompt_tokens     = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)

    cost_usd: float | None = None
    try:
        cost_usd = litellm.completion_cost(completion_response=resp)
    except Exception:
        pass  # cost unavailable for some providers

    model_used = getattr(resp, "model", primary)

    logger.info(
        "LLM response: model=%s intent=%s tokens=%s/%s cost=$%.6f elapsed_ms=%s",
        model_used, intent, prompt_tokens, completion_tokens,
        cost_usd or 0.0, elapsed_ms,
    )

    return LLMResult(
        answer=answer,
        model_used=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        elapsed_ms=elapsed_ms,
    )


def call_vision_llm(messages: list[dict], max_tokens: int = 512) -> tuple[str, str]:
    """Call the primary vision model with fallbacks, returning (description, model_used)."""
    # 1. Try primary gemini/gemini-flash-latest
    try:
        resp = litellm.completion(
            model=_PRIMARY_VISION,
            messages=messages,
            max_tokens=max_tokens,
        )
        if resp.choices and resp.choices[0].message.content:
            return resp.choices[0].message.content.strip(), _PRIMARY_VISION
    except Exception as e:
        logger.warning("Primary vision model %s failed: %s. Trying fallbacks...", _PRIMARY_VISION, e)

    # 2. Try fallbacks sequentially
    for m in _VISION_FALLBACKS:
        try:
            resp = litellm.completion(
                model=m,
                messages=messages,
                max_tokens=max_tokens,
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip(), m
        except Exception as e:
            logger.warning("Fallback vision model %s failed: %s", m, e)

    raise RuntimeError("All vision model attempts failed.")
