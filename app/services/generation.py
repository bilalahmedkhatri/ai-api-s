"""Direct generation using the existing DB chat selector and atomic key reservations.

No search, tools, end-user billing, or prompt rewriting. Keep this
strict response/deadline policy separate from the compatible query adapter.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import litellm

from app.core.config import settings
from app.services.key_manager import get_next_available_key, mark_key_quota_exceeded, mark_key_used
from app.services.model_router import MAX_MODEL_ATTEMPTS, LLMResult, _model_name, _pick_models
from app.services.query_telemetry import record_call

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@dataclass
class GenerationTrace:
    attempts: list[dict] = field(default_factory=list)
    result: LLMResult | None = None
    finish_reason: str = "unknown"


def _fits_budget(model, prompt: str) -> bool:
    name = _model_name(model)
    info = (
        litellm.model_cost.get(name)
        or litellm.model_cost.get(model.name)
        or litellm.model_cost.get(name.split("/", 1)[1], {})
    )
    input_limit = info.get("max_input_tokens")
    output_limit = info.get("max_output_tokens")
    # No token counter is used by the existing backend. UTF-8 bytes are a
    # conservative token estimate for the supported text adapters; 256 covers
    # chat framing. Reserve output inside the input window as well, since
    # metadata may describe a shared context. Unknown capacities fail closed.
    estimated_input = len(prompt.encode("utf-8")) + 256
    output = settings.generation_max_output_tokens
    return (
        isinstance(input_limit, int)
        and isinstance(output_limit, int)
        and output <= output_limit
        and estimated_input + output <= input_limit
    )


async def generate_text(
    prompt: str, *, db, request_id: str, trace: GenerationTrace | None = None
) -> tuple[str, str]:
    trace = trace if trace is not None else GenerationTrace()
    eligible = False
    attempted = 0
    request_started = time.monotonic()
    failure = GenerationError(503, "provider_unavailable", "No provider is currently available")
    for model in await _pick_models(db, "openai/gpt-4o-mini"):
        if attempted >= MAX_MODEL_ATTEMPTS:
            break
        name = _model_name(model)
        attempt = {"model": name, "provider": model.provider, "status": "skipped"}
        trace.attempts.append(attempt)
        if not _fits_budget(model, prompt):
            attempt.update(error_type="InsufficientContext", elapsed_ms=0)
            continue
        eligible = True
        key = await get_next_available_key(db, model.provider, reserve=True)
        if not key:
            attempt.update(error_type="NoEligibleKey", elapsed_ms=0)
            continue
        key_id, secret = key
        attempted += 1
        started = time.monotonic()
        response = None
        outcome_error = None
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=name,
                    api_key=secret,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=settings.generation_max_output_tokens,
                    timeout=settings.generation_provider_timeout_seconds,
                    num_retries=0,
                    max_retries=0,
                    fallbacks=[],
                    **{"no-log": True},
                ),
                timeout=settings.generation_provider_timeout_seconds,
            )
            try:
                choice = response.choices[0]
                message = choice.message
                reason = getattr(choice, "finish_reason", None)
                trace.finish_reason = (
                    reason
                    if reason
                    in {
                        "stop",
                        "length",
                        "max_tokens",
                        "max_output_tokens",
                        "content_filter",
                        "tool_calls",
                        "function_call",
                    }
                    else "unknown"
                )
                if reason == "content_filter" or getattr(message, "refusal", None):
                    trace.finish_reason = "refusal"
                    raise GenerationError(422, "provider_refusal", "Provider declined this request")
                if reason in {"length", "max_tokens", "max_output_tokens"}:
                    raise GenerationError(
                        502, "output_truncated", "Output budget exhausted; request a shorter output"
                    )
                if (
                    getattr(message, "tool_calls", None)
                    or getattr(message, "function_call", None)
                    or reason in {"tool_calls", "function_call"}
                ):
                    raise GenerationError(
                        502,
                        "invalid_provider_result",
                        "Provider returned a tool request instead of text",
                    )
                answer = message.content
                if not isinstance(answer, str) or not answer.strip():
                    raise GenerationError(502, "empty_provider_result", "Provider returned no text")
                answer.encode("utf-8")
            except (AttributeError, IndexError, TypeError, UnicodeEncodeError):
                raise GenerationError(
                    502, "invalid_provider_result", "Provider returned malformed text output"
                ) from None
        except asyncio.CancelledError as exc:
            outcome_error = exc
            attempt.update(status="cancelled", error_type="CancelledError")
            raise
        except Exception as exc:
            outcome_error = exc
            attempt.update(
                status="failed",
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", getattr(exc, "status", None)),
            )
            logger.warning(
                "Generation attempt request_id=%s model=%s error_type=%s elapsed_ms=%.1f",
                request_id,
                name,
                type(exc).__name__,
                (time.monotonic() - started) * 1000,
            )
            # An ambiguous timeout/connection loss may have generated content.
            # Never repeat it automatically on a different model.
            if isinstance(exc, GenerationError):
                attempt["error_code"] = exc.code
                raise
            if isinstance(exc, (TimeoutError, litellm.Timeout)):
                raise GenerationError(
                    504, "provider_timeout", "Provider timed out; completion is unknown"
                ) from None
            if isinstance(exc, litellm.APIConnectionError):
                raise GenerationError(
                    502,
                    "provider_connection_error",
                    "Provider connection failed; completion is unknown",
                ) from None
            if isinstance(exc, litellm.ContentPolicyViolationError):
                raise GenerationError(
                    422, "provider_refusal", "Provider declined this request"
                ) from None
            if getattr(exc, "status_code", None) == 429:
                await mark_key_quota_exceeded(db, key_id)
            # Existing bounded DB fallback order, only for explicit failures.
            continue
        finally:
            telemetry = record_call("chat", name, started, response=response, error=outcome_error)
            attempt.update(
                {
                    key: telemetry[key]
                    for key in ("prompt_tokens", "completion_tokens", "cost_usd", "elapsed_ms")
                }
            )
            if response is not None:
                try:
                    content = response.choices[0].message.content
                except (AttributeError, IndexError, TypeError, KeyError):
                    content = None
                # Save only text content, never reasoning, tool arguments or raw payloads.
                content = content if isinstance(content, str) else ""
                try:
                    content.encode("utf-8")
                except UnicodeEncodeError:
                    content = ""
                trace.result = LLMResult(
                    content,
                    getattr(response, "model", None) or name,
                    telemetry["prompt_tokens"],
                    telemetry["completion_tokens"],
                    telemetry["cost_usd"],
                    round((time.monotonic() - request_started) * 1000, 1),
                    trace.attempts,
                )
        attempt.update(
            status="completed",
            actual_model=trace.result.model_used,
            catalog_name=model.name,
            is_free=model.is_free,
        )
        try:
            await mark_key_used(db, key_id, reserved=True)
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "Generation bookkeeping request_id=%s error_type=%s", request_id, type(exc).__name__
            )
        logger.info(
            "Generation completed request_id=%s model=%s input_chars=%d output_chars=%d elapsed_ms=%.1f",
            request_id,
            name,
            len(prompt),
            len(answer),
            (time.monotonic() - started) * 1000,
        )
        return answer, "stop" if reason == "stop" else "unknown"
    if not eligible:
        raise GenerationError(
            422,
            "no_eligible_model",
            "No configured chat model has a known sufficient context/output budget; shorten the prompt or configure a compatible model",
        )
    if not attempted:
        raise GenerationError(
            503, "no_provider_key", "No eligible provider key is currently available"
        )
    raise failure
