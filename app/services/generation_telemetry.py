"""Durable provider attempts in existing job metadata, without credentials or payloads."""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from anyio import CancelScope
from fastapi import HTTPException
from sqlalchemy import select

from app.services.query_telemetry import measured_usage

attempt_scope = ContextVar("generation_attempt_scope", default=None)
active_attempt = ContextVar("generation_active_attempt", default=None)


def request_identity(request, app_id):
    """An authenticated app's opaque user reference, never a gateway users-table ID."""
    value = request.headers.get("X-External-User-ID")
    if value is not None and (not value.strip() or len(value) > 128 or not value.isprintable()):
        raise HTTPException(422, "X-External-User-ID must be a nonempty reference of at most 128 characters")
    return {"authorized_app_id": app_id, "external_user_id": value,
            "request_id": getattr(request.state, "request_id", None)}


def media_metadata(request, app_id, model, parameters):
    return json.dumps({"parameters": parameters or {}, "_telemetry": {
        "version": 1, "coverage_complete": True, "attempts": [],
        **request_identity(request, app_id),
        "model_snapshot": {"name": model.name, "provider": model.provider, "request_type": model.media_type},
    }})


def media_telemetry(identifier):
    from app.db.database import AsyncSessionLocal
    from app.models.db_models import MediaGeneration

    lock = asyncio.Lock()

    async def sink(event):
        async with lock:
            await persist_attempt(AsyncSessionLocal, MediaGeneration, [MediaGeneration.id == identifier],
                                  "extra_params_json", event)

    return telemetry_scope(sink, operation_id="outputs", model=None, provider=None)


async def finalize_media(db, audit):
    # Flush parent changes before refreshing JSON updated by short-lived attempt sessions.
    await db.flush()
    await db.refresh(audit)
    data = object_data(audit.extra_params_json)
    telemetry = data.setdefault("_telemetry", {})
    telemetry.update(attempt_summary(telemetry), finished_at=datetime.now(UTC).isoformat())
    audit.extra_params_json = json.dumps(data)
    await db.commit()


def object_data(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


@contextmanager
def telemetry_scope(sink, **identity):
    token = attempt_scope.set({"sink": sink, **identity})
    try:
        yield
    finally:
        attempt_scope.reset(token)


async def persist_attempt(factory, model, conditions, field, event):
    async with factory() as db:
        row = (await db.execute(select(model).where(*conditions).with_for_update()
                                .execution_options(populate_existing=True))).scalar_one_or_none()
        if row is None:
            raise RuntimeError("Attempt record is no longer owned")
        data = object_data(getattr(row, field))
        telemetry = data.setdefault("_telemetry", {})
        attempts = telemetry.setdefault("attempts", [])
        prior = next((i for i, item in enumerate(attempts) if item["attempt_id"] == event["attempt_id"]), None)
        if prior is None:
            attempts.append(dict(event))
        else:
            attempts[prior] = dict(event)
        telemetry.update(attempt_summary(telemetry))
        setattr(row, field, json.dumps(data))
        await db.commit()


@asynccontextmanager
async def provider_attempt(kind, *, model=None, provider=None, operation_id=None):
    """One generation invocation; polling/downloads stay inside the same attempt."""
    scope = attempt_scope.get()
    measurement = {}
    if scope is None:
        yield measurement
        return
    event = {"attempt_id": uuid.uuid4().hex, "operation_id": operation_id or scope["operation_id"],
             "kind": kind, "model": model or scope["model"], "provider": provider or scope["provider"],
             "status": "processing",
             "prompt_tokens": None, "completion_tokens": None, "cost_usd": None}
    state = {"scope": scope, "event": event, "measurement": measurement, "started": None}
    token = active_attempt.set(state)
    try:
        yield measurement
    except BaseException as exc:
        event.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                     error_type=type(exc).__name__)
        raise
    else:
        event["status"] = "completed"
    finally:
        active_attempt.reset(token)
        if state["started"] is not None:
            event.update({key: measurement[key] for key in
                          ("prompt_tokens", "completion_tokens", "cost_usd", "status_code") if key in measurement})
            event.update(finished_at=datetime.now(UTC).isoformat(),
                         elapsed_ms=round((time.monotonic() - state["started"]) * 1000, 1),
                         cost_source="litellm_estimate" if event.get("cost_usd") is not None else "not_recorded")
            # Cancellation must not discard an already-observed outbound attempt.
            with CancelScope(shield=True):
                await scope["sink"](event)


async def begin_attempt():
    state = active_attempt.get()
    if state is not None and state["started"] is None:
        state["event"]["started_at"] = datetime.now(UTC).isoformat()
        # Commit before I/O; a hard stop then leaves an unfinished attempt visible.
        await state["scope"]["sink"](state["event"])
        state["started"] = time.monotonic()


def observe_http(response, *, gemini=False):
    state = active_attempt.get()
    if state is not None:
        http_usage(state["measurement"], response, gemini=gemini)


def http_usage(measurement, response, *, gemini=False):
    measurement["status_code"] = response.status_code
    try:
        data = response.json()
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    if gemini:
        usage = data.get("usageMetadata") or {}
        for source, target in (("promptTokenCount", "prompt_tokens"), ("candidatesTokenCount", "completion_tokens")):
            value = usage.get(source) if isinstance(usage, dict) else None
            if type(value) is int and value >= 0:
                measurement[target] = value
        # Audio-token pricing differs from chat pricing. Do not guess a bill.
    else:
        measurement.update({k: v for k, v in measured_usage(data, None).items() if v is not None})


def attempt_summary(telemetry):
    attempts = telemetry.get("attempts", [])
    known = [a["cost_usd"] for a in attempts if a.get("cost_usd") is not None]
    complete = telemetry.get("version") == 1 and telemetry.get("coverage_complete") is True
    complete = complete and all(a.get("status") in {"completed", "failed", "cancelled"} for a in attempts)
    return {"attempts_complete": complete, "known_provider_cost_usd": sum(known) if known else 0 if complete and not attempts else None,
            "provider_cost_complete": complete and all(a.get("cost_usd") is not None for a in attempts)}
