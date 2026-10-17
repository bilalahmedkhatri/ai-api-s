"""Request-local provider estimates; never infer an invoice from missing usage."""

import math
import time
from contextvars import ContextVar

import litellm

provider_calls = ContextVar("query_provider_calls", default=None)


def measured_usage(response, model):
    if isinstance(response, Exception) and getattr(response, "usage", None) is None:
        try:
            payload = response.response.json()
            response = {"usage": payload.get("usage"), "model": payload.get("model")}
        except (AttributeError, ValueError, TypeError):
            response = None
    usage = getattr(response, "usage", None)
    if isinstance(response, dict):
        usage = response.get("usage")
    def value(name):
        number = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return number if type(number) is int and number >= 0 else None
    cost = None
    if usage is not None:
        try:
            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            # Some provider exceptions expose usage but no completion response.
            if value("prompt_tokens") is not None and value("completion_tokens") is not None:
                try:
                    cost = sum(litellm.cost_per_token(model=model, prompt_tokens=value("prompt_tokens"),
                                                      completion_tokens=value("completion_tokens")))
                except Exception:
                    pass
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            cost = None
    return {"prompt_tokens": value("prompt_tokens"), "completion_tokens": value("completion_tokens"),
            "cost_usd": cost}


def record_call(kind, model, started, *, response=None, error=None, configured_cost=None):
    calls = provider_calls.get()
    usage = measured_usage(response if response is not None else error, model)
    if configured_cost is not None:
        usage["cost_usd"] = configured_cost
    record = {"kind": kind, "model": model, "status": "failed" if error else "completed",
              "elapsed_ms": round((time.monotonic() - started) * 1000, 1), **usage,
              "cost_source": "configured_per_call" if configured_cost is not None else "litellm_estimate",
              "error_type": type(error).__name__ if error else None}
    if calls is not None:
        calls.append(record)
    return record


def cost_summary(calls, *, interrupted=False):
    unknown = sum(call["cost_usd"] is None for call in calls) + int(interrupted)
    known = sum(call["cost_usd"] for call in calls if call["cost_usd"] is not None)
    other = [call["cost_usd"] for call in calls if call["kind"] != "chat" or call["status"] != "completed"]
    return {"provider_calls": calls, "known_provider_cost_usd": known,
            "total_provider_cost_usd": None if unknown else known,
            "unknown_cost_count": unknown, "provider_cost_complete": not unknown,
            "other_provider_cost_usd": None if interrupted or any(cost is None for cost in other) else sum(other),
            "cost_scope": "observed_provider_estimates"}
