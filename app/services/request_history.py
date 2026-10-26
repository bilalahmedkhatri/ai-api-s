"""Read-only admin history over existing generation tables; no storage/provider calls."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    String,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    type_coerce,
    union_all,
)
from sqlalchemy import (
    null as sql_null,
)
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH

from app.models.db_models import (
    AIModel,
    AuthorizedApp,
    FinalResponse,
    MediaGeneration,
    MediaModel,
    Query,
    TTSChunk,
    TTSJob,
)
from app.services.generation_telemetry import attempt_summary, object_data

STATUSES = {"queued", "processing", "completed", "failed", "cancelled", "rejected"}
TYPES = {"query", "generate", "tts", "image", "video"}


def metadata(db, column):
    return cast(column, JSON) if db.get_bind().dialect.name == "postgresql" else type_coerce(column, JSON)


def normalize_status(value):
    return case((value == "pending", "queued"), (value == "success", "completed"),
                (value == "timeout", "failed"), (value.in_(STATUSES), value), else_="unknown")


def projection(db):
    """A common SQL projection used by both paginated history and aggregate counts."""
    qm, fm = metadata(db, Query.meta_fields), metadata(db, FinalResponse.meta_fields)
    generation = metadata(db, TTSJob.generation_json)
    snapshot = generation["model_snapshot"]
    tts_telemetry = generation["_telemetry"]
    media_telemetry = metadata(db, MediaGeneration.extra_params_json)["_telemetry"]
    media_snapshot = media_telemetry["model_snapshot"]
    latest = (select(func.max(FinalResponse.id)).where(FinalResponse.query_id == Query.id)
              .correlate(Query).scalar_subquery())
    # Trace attempts are a second view of the same calls, not additional usage.
    if db.get_bind().dialect.name == "postgresql":
        completed = func.jsonb_path_query_first(
            cast(fm["model_attempts"], JSONB), cast('$[*] ? (@.status == "completed")', JSONPATH))
        attempt_provider = type_coerce(completed, JSONB)["provider"].as_string()
    else:
        attempts = func.json_each(fm["model_attempts"]).table_valued("key", "value").alias("attempts")
        attempt = type_coerce(attempts.c.value, JSON)
        attempt_provider = (select(attempt["provider"].as_string())
                            .where(attempt["status"].as_string() == "completed")
                            .select_from(attempts).limit(1).correlate(FinalResponse).scalar_subquery())
    catalog_provider = (select(AIModel.provider).where(AIModel.name == FinalResponse.model_used)
                        .correlate(FinalResponse).scalar_subquery())
    model = case((FinalResponse.model_used.in_(["failed", "rule", "cache"]), None),
                 else_=FinalResponse.model_used)
    # SQL NULL must not share a bound parameter across numeric/boolean UNION columns.
    null = sql_null()

    def row(prefix, native, kind, created, source_status, model, provider, provenance,
            app_id, owner_id, duration, duration_source, tokens_in=null, tokens_out=null,
            cost=null, complete=null, accounting=null, external_user_id=null):
        values = {
            "record_id": literal(prefix + ":") + cast(native, String), "native_id": cast(native, String),
            "request_type": kind, "created_at": created, "source_status": source_status,
            "status": normalize_status(source_status), "model": model, "provider": provider,
            "model_provenance": provenance, "authorized_app_id": app_id, "owner_user_id": owner_id,
            "duration_ms": duration, "duration_source": duration_source, "input_tokens": tokens_in,
            "output_tokens": tokens_out, "known_provider_cost_usd": cost, "provider_cost_complete": complete,
            "usage_accounting_status": accounting,
            "external_user_id": external_user_id,
        }
        return select(*(value.label(name) for name, value in values.items()))

    owner = select(AuthorizedApp.owner_user_id).where(AuthorizedApp.id == Query.authorized_app_id).correlate(Query).scalar_subquery()
    query = row(
        "query", Query.id, func.coalesce(qm["request_type"].as_string(), "query"),
        Query.created_at, qm["status"].as_string(), model,
        func.coalesce(attempt_provider, catalog_provider),
        case((attempt_provider.is_not(None), "recorded_attempt"),
             (catalog_provider.is_not(None), "catalog_derived"), else_="not_recorded"),
        Query.authorized_app_id, owner, qm["duration_ms"].as_float(), literal("recorded_request"),
        FinalResponse.prompt_tokens, FinalResponse.completion_tokens,
        func.coalesce(qm["known_provider_cost_usd"].as_float(), FinalResponse.cost_usd),
        qm["provider_cost_complete"].as_boolean(), qm["usage_accounting_status"].as_string(),
        external_user_id=qm["external_user_id"].as_string(),
    ).select_from(Query).outerjoin(FinalResponse, FinalResponse.id == latest)
    tts_owner = (select(AuthorizedApp.owner_user_id).where(AuthorizedApp.id == TTSJob.authorized_app_id)
                 .correlate(TTSJob).scalar_subquery())
    # Terminal boundaries are recorded by the worker, never inferred from updated_at.
    tts_duration = case((TTSJob.status.in_(["completed", "failed", "cancelled"]),
                         tts_telemetry["duration_ms"].as_float()), else_=None)
    tts = row("tts", TTSJob.job_id, literal("tts"), TTSJob.created_at, TTSJob.status,
              snapshot["name"].as_string(), snapshot["provider"].as_string(),
              literal("job_snapshot"), TTSJob.authorized_app_id, tts_owner,
              tts_duration,
              case((tts_duration.is_not(None), "submission_to_latest_terminal"), else_="not_recorded"),
              cost=tts_telemetry["known_provider_cost_usd"].as_float(),
              complete=case((TTSJob.status.in_(["completed", "failed", "cancelled"]),
                             tts_telemetry["provider_cost_complete"].as_boolean()), else_=None),
              external_user_id=tts_telemetry["external_user_id"].as_string())
    media = row("media", MediaGeneration.id, func.coalesce(media_snapshot["request_type"].as_string(), MediaModel.media_type),
                MediaGeneration.created_at, MediaGeneration.status,
                func.coalesce(media_snapshot["name"].as_string(), MediaModel.name),
                func.coalesce(media_snapshot["provider"].as_string(), MediaModel.provider),
                case((media_snapshot["name"].as_string().is_not(None), "execution_snapshot"), else_="catalog_derived"),
                media_telemetry["authorized_app_id"].as_integer(), MediaGeneration.user_id,
                MediaGeneration.elapsed_ms, literal("recorded_generation"),
                cost=media_telemetry["known_provider_cost_usd"].as_float(),
                complete=case((MediaGeneration.status.in_(["success", "completed", "failed", "timeout", "cancelled"]),
                               media_telemetry["provider_cost_complete"].as_boolean()), else_=None),
                external_user_id=media_telemetry["external_user_id"].as_string())
    media = media.select_from(MediaGeneration).outerjoin(MediaModel, MediaModel.id == MediaGeneration.model_id)
    return union_all(query, tts, media).subquery("history")


def filtered(db, *, request_type=None, status=None, model=None, provider=None, search=None,
             authorized_app_id=None, owner_user_id=None, date_from=None, date_to=None):
    end = date_to or datetime.now(UTC)
    start = date_from or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None or not start < end or end - start > timedelta(days=366):
        raise HTTPException(422, "Use timezone-aware dates with from < to and a maximum 366-day range")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if request_type is not None and request_type not in TYPES:
        raise HTTPException(422, "Unknown request type")
    if status is not None and status not in STATUSES | {"unknown"}:
        raise HTTPException(422, "Unknown request status")
    source = projection(db)
    conditions = [source.c.created_at >= start, source.c.created_at < end]
    if search:
        conditions.append(or_(*(source.c[name].contains(search, autoescape=True)
                                for name in ("record_id", "model", "provider"))))
    for name, value in {"request_type": request_type, "status": status, "model": model, "provider": provider,
                            "authorized_app_id": authorized_app_id, "owner_user_id": owner_user_id}.items():
        if value is not None:
            conditions.append(source.c[name] == value)
    return select(source).where(*conditions).subquery("filtered_history"), start, end


def summary_record(row):
    result = dict(row)
    if result["created_at"].tzinfo is None:
        result["created_at"] = result["created_at"].replace(tzinfo=UTC)
    result["customer_chargeable_tokens"] = 0 if result["status"] in {"failed", "cancelled", "rejected"} else None
    result["customer_tokens_basis"] = "zero_charge_policy" if result["customer_chargeable_tokens"] == 0 else "not_reported"
    result["provider_cost_source"] = "observed_estimate_or_legacy_final" if result["known_provider_cost_usd"] is not None else "not_recorded"
    return result


async def history_page(db, filters, page=1, page_size=20):
    source, start, end = filtered(db, **filters)
    total = (await db.execute(select(func.count()).select_from(source))).scalar_one()
    rows = (await db.execute(select(source).order_by(source.c.created_at.desc(), source.c.record_id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).mappings().all()
    return {"page": page, "page_size": page_size, "total": total, "date_from": start,
            "date_to": end, "items": [summary_record(row) for row in rows]}


def metric_columns(c):
    return [
        func.count().label("requests"), func.count(c.duration_ms).label("duration_samples"),
        func.avg(c.duration_ms).label("average_duration_ms"),
        func.sum(c.known_provider_cost_usd).label("known_provider_cost_usd"),
        func.count(c.known_provider_cost_usd).label("cost_samples"),
        func.coalesce(func.sum(case((c.provider_cost_complete.is_(True), 1), else_=0)), 0).label("complete_cost_requests"),
        func.sum(c.input_tokens).label("recorded_input_tokens"),
        func.sum(c.output_tokens).label("recorded_output_tokens"),
        func.count(c.input_tokens).label("input_token_samples"),
        func.count(c.output_tokens).label("output_token_samples"),
    ]


def daily_statement(db, source):
    c = source.c
    # Reuse this expression in SELECT/GROUP BY: distinct timezone binds break PostgreSQL grouping.
    day = (func.date(func.timezone("UTC", c.created_at)) if db.get_bind().dialect.name == "postgresql"
           else func.date(c.created_at)).label("day")
    return (select(day, *metric_columns(c),
                   func.sum(case((c.status == "completed", 1), else_=0)).label("completed"),
                   func.sum(case((c.status == "failed", 1), else_=0)).label("failed"))
            .group_by(day).order_by(day))


async def history_summary(db, filters):
    source, start, end = filtered(db, **filters)
    c = source.c
    totals = (await db.execute(select(*metric_columns(c)))).mappings().one()
    breakdowns = {}
    for name in ("status", "request_type", "model", "provider"):
        rows = (await db.execute(select(c[name].label("value"), func.count().label("count"))
                                .group_by(c[name]).order_by(c[name]))).mappings().all()
        breakdowns[name] = [dict(row) for row in rows]
    counts = {row["value"]: row["count"] for row in breakdowns["status"]}
    terminal = counts.get("completed", 0) + counts.get("failed", 0)
    daily = (await db.execute(daily_statement(db, source))).mappings().all()
    by_day = {str(row["day"]): dict(row) for row in daily}
    series = []
    day = start.date()
    while day <= (end - timedelta(microseconds=1)).date():
        point = by_day.get(day.isoformat(), {
            "day": day.isoformat(), "requests": 0, "completed": 0, "failed": 0,
            "average_duration_ms": None, "duration_samples": 0, "known_provider_cost_usd": None,
            "cost_samples": 0, "complete_cost_requests": 0,
            "recorded_input_tokens": None, "recorded_output_tokens": None,
            "input_token_samples": 0, "output_token_samples": 0,
        })
        resolved = point["completed"] + point["failed"]
        point["success_rate"] = point["completed"] * 100 / resolved if resolved else None
        series.append(point)
        day += timedelta(days=1)
    return {**dict(totals), "date_from": start, "date_to": end, "scope": "AI parent requests",
            "cost_scope": "known_subtotal_not_provider_bill", "breakdowns": breakdowns,
            "token_scope": "latest_final_response_not_all_provider_attempts",
            "series": series, "series_scope": "UTC creation-date cohorts; current parent status",
            "success_rate": counts.get("completed", 0) * 100 / terminal if terminal else None}


ATTEMPT_FIELDS = {"kind", "model", "actual_model", "provider", "status", "error_type", "error_code",
                  "status_code", "elapsed_ms", "prompt_tokens", "completion_tokens", "cost_usd", "cost_source",
                  "attempt_id", "operation_id", "started_at", "finished_at"}


async def history_detail(db, record_id):
    source = projection(db)
    row = (await db.execute(select(source).where(source.c.record_id == record_id))).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "AI request not found")
    result = summary_record(row)
    kind, identifier = record_id.split(":", 1)
    if kind == "query":
        # Reuse the existing latest-response selection; do not return its raw metadata.
        from app.api.v1.query.router import _request_records

        query = await db.get(Query, int(identifier))
        record = (await _request_records(db, [query]))[0]
        meta, routing = record["request"], record["routing"]
        calls = meta.get("provider_calls")
        attempts = calls if isinstance(calls, list) and calls else routing.get("model_attempts", [])
        result.update(input=record["input"], output=record["answer"],
                      error_type=meta.get("error_type"), error_code=meta.get("error_code"),
                      attempts=[{k: v for k, v in item.items() if k in ATTEMPT_FIELDS}
                                for item in attempts if isinstance(item, dict)],
                      attempts_source="provider_calls" if calls else "model_trace",
                      attempts_complete=meta.get("provider_telemetry_version") == 1 and meta.get("attempts_complete") is True)
    elif kind == "tts":
        job = (await db.execute(select(TTSJob).where(TTSJob.job_id == identifier))).scalar_one()
        config = object_data(job.generation_json)
        chunks = (await db.execute(select(TTSChunk).where(TTSChunk.job_id == identifier)
                                   .order_by(TTSChunk.chunk_index))).scalars().all()
        result.update(input=job.text, voice=config.get("voice"), audio_duration_seconds=job.duration_sec,
                      size_bytes=job.size_bytes, has_error=bool(job.error_message), attempts_complete=False,
                      chunks=[{"index": c.chunk_index, "status": c.status, "recorded_chunk_attempts": c.attempts,
                                   "has_error": bool(c.error_message)} for c in chunks])
        telemetry = config.get("_telemetry", {})
    else:
        media = await db.get(MediaGeneration, int(identifier))
        result.update(input=media.prompt, width=media.width, height=media.height,
                      media_duration_seconds=media.duration_s, output_count=media.num_outputs,
                      has_error=bool(media.error_message), attempts_complete=False)
        telemetry = object_data(media.extra_params_json).get("_telemetry", {})
    if kind != "query":
        metrics = attempt_summary(telemetry)
        metrics["attempts_complete"] = metrics["attempts_complete"] and result["status"] in {"completed", "failed", "cancelled"}
        metrics["provider_cost_complete"] = metrics["provider_cost_complete"] and metrics["attempts_complete"]
        result.update(attempts=[{k: v for k, v in a.items() if k in ATTEMPT_FIELDS}
                                for a in telemetry.get("attempts", []) if isinstance(a, dict)],
                      attempts_source="provider_calls" if telemetry.get("version") == 1 else "not_recorded",
                      **metrics)
    return result
