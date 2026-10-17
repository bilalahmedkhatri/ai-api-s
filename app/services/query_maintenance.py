"""Bounded request recovery and transactionally idempotent daily accounting."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, cast, func, select, type_coerce, update

from app.db.database import AsyncSessionLocal
from app.models.db_models import FinalResponse, Query
from app.services.usage_tracker import record_model_usage

logger = logging.getLogger(__name__)
QUERY_TIMEOUT_SECONDS = 180
STALE_SECONDS = 600  # Greater than the enforced request timeout plus DB finalization.


def metadata_expression(db):
    return (cast(Query.meta_fields, JSON) if db.get_bind().dialect.name == "postgresql"
            else type_coerce(Query.meta_fields, JSON))


async def account_query(db, query_id):
    row = (await db.execute(select(Query).where(Query.id == query_id)
                            .execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None or row.meta.get("status") != "completed" or row.meta.get("usage_accounting_status") != "pending":
        return False
    final = (await db.execute(select(FinalResponse).where(FinalResponse.query_id == query_id)
                              .order_by(FinalResponse.id.desc()).limit(1))).scalar_one_or_none()
    if (final is None or final.meta.get("status") != "completed"
            or final.meta.get("cache_hit") or row.authorized_app_id is None):
        return False
    completed = next((a for a in final.meta.get("model_attempts", []) if a["status"] == "completed"), None)
    if not completed:
        return False
    metadata = {**row.meta, "usage_accounting_status": "recorded"}
    metadata.pop("usage_accounting_error_type", None)
    # Compare-and-set holds the row until usage and marker commit together.
    claimed = (await db.execute(update(Query).where(Query.id == query_id, Query.meta_fields == row.meta_fields)
                               .values(meta_fields=json.dumps(metadata)).returning(Query.id)
                               .execution_options(synchronize_session="fetch"))).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        return False
    try:
        await record_model_usage(db, model_name=completed["catalog_name"], provider=completed["provider"],
                                 is_free=completed["is_free"], website_origin=f"app:{row.authorized_app_id}",
                                 prompt_tokens=completed.get("prompt_tokens") or 0,
                                 completion_tokens=completed.get("completion_tokens") or 0,
                                 usage_date=row.created_at.date(), commit=False)
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def maintain_queries(db):
    now = datetime.now(UTC)
    status = metadata_expression(db)["status"].as_string()
    stale = (await db.execute(select(Query.id, Query.meta_fields).where(status == "processing",
                            Query.created_at < now - timedelta(seconds=STALE_SECONDS))
                            .order_by(Query.id).limit(100))).all()
    for identifier, original_metadata in stale:
        metadata = {**json.loads(original_metadata), "status": "failed", "http_status": 503, "error_type": "WorkerInterrupted",
                    "recovered_at": now.isoformat(), "usage_accounting_status": "not_applicable",
                    "provider_cost_complete": False, "total_provider_cost_usd": None}
        claimed = (await db.execute(update(Query).where(Query.id == identifier, Query.meta_fields == original_metadata)
                                   .values(meta_fields=json.dumps(metadata)).returning(Query.id)
                                   .execution_options(synchronize_session=False))).scalar_one_or_none()
        if claimed is None:
            await db.rollback()
            continue
        final = (await db.execute(select(FinalResponse).where(FinalResponse.query_id == identifier)
                                  .order_by(FinalResponse.id.desc()).limit(1))).scalar_one_or_none()
        if final is None:
            final = FinalResponse(query_id=identifier, generated_output="[Generation interrupted]", model_used="failed")
            db.add(final)
        final.meta = {**final.meta, "status": "failed", "error_type": "WorkerInterrupted"}
        await db.commit()
        logger.warning("Recovered interrupted query query_id=%s", identifier)
    pending = (await db.execute(select(Query.id).where(status == "completed",
                              metadata_expression(db)["usage_accounting_status"].as_string() == "pending")
                              .order_by(func.coalesce(metadata_expression(db)["usage_accounting_attempted_at"].as_string(), ""),
                                        Query.id).limit(100))).scalars().all()
    for identifier in pending:
        retry_error = None
        try:
            if await account_query(db, identifier):
                continue
        except Exception as exc:
            await db.rollback()
            retry_error = type(exc).__name__
            logger.warning("Query accounting deferred query_id=%s error_type=%s", identifier, type(exc).__name__)
        # Rotate deferred rows behind unattempted rows so one bad record cannot starve the queue.
        row = (await db.execute(select(Query).where(Query.id == identifier)
                                .execution_options(populate_existing=True))).scalar_one_or_none()
        if row is not None and row.meta.get("usage_accounting_status") == "pending":
            metadata = {**row.meta, "usage_accounting_attempted_at": now.isoformat(),
                        "usage_accounting_error_type": retry_error or "MissingUsageMetadata"}
            await db.execute(update(Query).where(Query.id == identifier, Query.meta_fields == row.meta_fields)
                             .values(meta_fields=json.dumps(metadata)).execution_options(synchronize_session="fetch"))
            await db.commit()


async def query_maintenance_loop():
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await maintain_queries(db)
        except Exception as exc:
            logger.warning("Query maintenance deferred error_type=%s", type(exc).__name__)
        await asyncio.sleep(30)
