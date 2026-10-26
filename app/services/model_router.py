"""DB-backed asynchronous chat routing; the separate vision helper stays compatible."""

import logging
import time
from dataclasses import dataclass

import litellm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AIModel, ModelEndpoint
from app.services.key_manager import get_next_available_key, mark_key_quota_exceeded, mark_key_used
from app.services.model_endpoints import chat_options
from app.services.query_telemetry import record_call

logger = logging.getLogger(__name__)
LLM_TIMEOUT = 15
MAX_MODEL_ATTEMPTS = 4
_SECOND_CHAT_MODEL = "replicate/openai/gpt-4o-mini"
_PRIMARY_VISION = "gemini/gemini-flash-latest"
_VISION_FALLBACKS = ["openrouter/google/gemma-4-31b-it:free", "openrouter/free"]


@dataclass
class LLMResult:
    answer: str
    model_used: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    elapsed_ms: float
    attempts: list[dict]


class ModelRoutingError(RuntimeError):
    def __init__(self, attempts):
        super().__init__("No available chat model completed the request")
        self.attempts = attempts


def _model_name(model):
    prefix = model.provider.strip().lower() + "/"
    return model.name if model.name.startswith(prefix) else prefix + model.name


async def _pick_models(db, preferred):
    rows = (await db.execute(select(AIModel).where(
        AIModel.is_active.is_(True), AIModel.provider != "openrouter",
        (~AIModel.endpoints.any()) | AIModel.endpoints.any(
            (ModelEndpoint.model_type == "chat") & ModelEndpoint.is_active.is_(True)
            & (ModelEndpoint.adapter == "litellm")),
    ))).scalars().all()
    compatible = []
    for model in rows:
        name = _model_name(model)
        if model.provider.strip().lower() == "openrouter":
            continue
        info = litellm.model_cost.get(name) or litellm.model_cost.get(model.name) or litellm.model_cost.get(name.split("/", 1)[1], {})
        if name == _SECOND_CHAT_MODEL:
            # This explicitly requested hosted GPT shares its known chat capability.
            info = info or litellm.model_cost.get("gpt-4o-mini", {})
        if info.get("mode") == "chat":
            compatible.append(model)
    compatible.sort(key=lambda m: (
        _model_name(m) != preferred, not m.is_free,
        m.prompt_price_per_1k + m.completion_price_per_1k, m.id,
    ))
    second = next((m for m in compatible if _model_name(m) == _SECOND_CHAT_MODEL), None)
    if second is not None and compatible[0] is not second:
        compatible.remove(second)
        compatible.insert(1, second)
    # Bound actual provider calls later; unavailable keys must not hide fallbacks.
    return compatible


async def call_llm(
    prompt: str,
    intent: str,
    *,
    db: AsyncSession,
    force_model: str = "openai/gpt-4o-mini",
    max_tokens: int = 1024,
) -> LLMResult:
    """Try active DB chat models with provider-scoped DB keys and sanitized telemetry."""
    started = time.monotonic()
    attempts = []
    provider_calls = 0
    for model in await _pick_models(db, force_model):
        if provider_calls >= MAX_MODEL_ATTEMPTS:
            break
        name = _model_name(model)
        attempt = {"model": name, "provider": model.provider, "status": "skipped"}
        attempts.append(attempt)
        key = await get_next_available_key(db, model.provider, reserve=True)
        if not key:
            attempt.update(error_type="NoEligibleKey", elapsed_ms=0)
            continue
        key_id, secret = key
        provider_calls += 1
        attempt_start = time.monotonic()
        response = None
        try:
            options = await chat_options(db, model)
            response = await litellm.acompletion(
                model=name, api_key=secret,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens, timeout=LLM_TIMEOUT, num_retries=0, **options,
            )
            answer = (response.choices[0].message.content or "").strip()
            if not answer:
                raise ValueError("Empty provider answer")
        except Exception as exc:
            telemetry = record_call("chat", name, attempt_start, response=response, error=exc, provider=model.provider)
            attempt.update({key: telemetry[key] for key in ("prompt_tokens", "completion_tokens", "cost_usd")})
            attempt.update(status="failed", error_type=type(exc).__name__,
                           status_code=getattr(exc, "status_code", None),
                           elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 1))
            logger.warning("Chat model attempt failed model=%s error_type=%s status=%s elapsed_ms=%s",
                           name, attempt["error_type"], attempt["status_code"], attempt["elapsed_ms"])
            if getattr(exc, "status_code", None) == 429:
                await mark_key_quota_exceeded(db, key_id)
            continue
        telemetry = record_call("chat", name, attempt_start, response=response, provider=model.provider)
        prompt_tokens, completion_tokens, cost = (telemetry[k] for k in ("prompt_tokens", "completion_tokens", "cost_usd"))
        actual_model = getattr(response, "model", None) or name
        attempt.update(status="completed", actual_model=actual_model,
                       catalog_name=model.name, is_free=model.is_free,
                       prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                       cost_usd=cost, elapsed_ms=round((time.monotonic() - attempt_start) * 1000, 1))
        try:
            await mark_key_used(db, key_id, reserved=True)
        except Exception as exc:
            await db.rollback()
            attempt["key_bookkeeping_error_type"] = type(exc).__name__
            logger.warning("Key last-use bookkeeping failed model=%s error_type=%s", name, type(exc).__name__)
        logger.info("Chat model completed model=%s intent=%s elapsed_ms=%s", name, intent, attempt["elapsed_ms"])
        return LLMResult(answer, actual_model, prompt_tokens, completion_tokens, cost,
                         round((time.monotonic() - started) * 1000, 1), attempts)
    raise ModelRoutingError(attempts)


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
