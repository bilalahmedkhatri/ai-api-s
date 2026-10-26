"""POST /api/v1/query — main entry point for the AI gateway pipeline."""

import asyncio
import json
import logging
import time
from typing import Literal

from anyio import CancelScope
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi import Query as QueryParam
from sqlalchemy import JSON, cast, func, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.query.schemas import QueryRequest, QueryResponse
from app.core.auth import get_current_api_key
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, FinalResponse, Query, SearchResult
from app.services.dispatcher import process_query
from app.services.generation_telemetry import request_identity
from app.services.query_maintenance import QUERY_TIMEOUT_SECONDS, account_query
from app.services.query_telemetry import cost_summary, provider_calls

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["query"])


@router.post("/", response_model=QueryResponse, summary="Process a query through the AI gateway")
async def query_endpoint(
    req: QueryRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    cache_control: str | None = Header(default=None),
) -> QueryResponse:
    """
    Full pipeline:
    1. Third-party AccessAPIKey and endpoint permission validation
    2. Upstash Redis cache lookup (bypass with Cache-Control: no-cache)
    3. Rule engine (SQLite 1hr cache + regex fast path)
    4. Semantic router (cosine similarity → intent classification)
    5. Search aggregation (Tavily / Brave / DDG)
    6. RAG prompt + active DB chat models, provider keys and async fallbacks
    7. DB persistence (tokens, cost, telemetry)
    8. Write result to Upstash with dynamic TTL
    """
    no_cache = cache_control is not None and "no-cache" in cache_control.lower()

    response.headers["Cache-Control"] = "no-store"
    started = time.monotonic()
    client_name = api_key.client_name
    q_row = Query(raw_query=req.query, input_type=req.input_type,
                  authorized_app_id=api_key.authorized_app_id)
    q_row.meta = {"request_id": getattr(request.state, "request_id", None),
                  "api_key_id": api_key.id, "status": "processing",
                  **request_identity(request, api_key.authorized_app_id)}
    db.add(q_row)
    await db.commit()
    query_id = q_row.id
    request.state.query_record_id = query_id
    calls = []
    telemetry_token = provider_calls.set(calls)
    error_status = None
    error_type = None
    try:
        async with asyncio.timeout(QUERY_TIMEOUT_SECONDS):
            result = await process_query(req, db, no_cache=no_cache, query_row=q_row)
    except asyncio.CancelledError:
        error_status, error_type = 499, "CancelledError"
        with CancelScope(shield=True):
            await db.rollback()
        raise
    except Exception as exc:
        error_status = exc.status_code if isinstance(exc, HTTPException) else 503
        error_type = type(exc).__name__
        logger.warning("Query failed query_id=%s error_type=%s status=%s", query_id, error_type, error_status)
        # Recover the initial committed request even if provider/DB work rolled back.
        await db.rollback()
        raise HTTPException(error_status, "AI generation is temporarily unavailable",
                            headers={"Cache-Control": "no-store"}) from None
    finally:
        provider_calls.reset(telemetry_token)
        with CancelScope(shield=True):
            q_row = await db.get(Query, query_id)
            final = (await db.execute(select(FinalResponse).where(FinalResponse.query_id == query_id)
                                     .order_by(FinalResponse.id.desc()).limit(1))).scalar_one_or_none()
            request_status = "cancelled" if error_status == 499 else "failed" if error_status else "completed"
            if error_status and final is None:
                final = FinalResponse(query_id=q_row.id, generated_output="[Generation failed]", model_used="failed")
                db.add(final)
            if error_status and final is not None:
                final.meta = {**final.meta, "status": request_status, "error_type": error_type}
            q_row.meta = {**q_row.meta, "status": request_status,
                          "http_status": error_status or 200, "error_type": error_type,
                          "duration_ms": round((time.monotonic() - started) * 1000, 1),
                          **cost_summary(calls, interrupted=error_type in {"CancelledError", "TimeoutError"}),
                          "usage_accounting_status": "pending" if not error_status and final is not None
                          and not final.meta.get("cache_hit")
                          and any(a["status"] == "completed" for a in final.meta.get("model_attempts", []))
                          else "not_applicable"}
            await db.commit()

    # Only a completed generation may contribute to user token totals. Its answer
    # and usage are durable already; optional accounting cannot turn it into 503.
    if result.routing.model_attempts and not result.routing.cache_hit:
        completed = next((a for a in result.routing.model_attempts if a["status"] == "completed"), None)
        if completed:
            try:
                await account_query(db, query_id)
            except Exception as exc:
                await db.rollback()
                logger.warning("Query accounting pending query_id=%s error_type=%s", query_id, type(exc).__name__)

    logger.info(
        "Query complete",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "query_id": result.query_id,
            "api_key_client": client_name,
            "cache_hit": result.routing.cache_hit,
            "elapsed_ms": result.routing.elapsed_ms,
        },
    )
    return result


async def _request_records(db, rows):
    ids = [row.id for row in rows]
    finals = (await db.execute(select(FinalResponse).where(FinalResponse.query_id.in_(ids))
                              .order_by(FinalResponse.id.desc()))).scalars().all() if ids else []
    latest = {}
    for final in finals:
        latest.setdefault(final.query_id, final)
    records = []
    for row in rows:
        final = latest.get(row.id)
        records.append({
            "query_id": row.id, "input": row.raw_query, "input_type": row.input_type,
            "authorized_app_id": row.authorized_app_id,
            "created_at": row.created_at, "request": row.meta,
            "status": row.meta.get("status"), "duration_ms": row.meta.get("duration_ms"),
            "answer": final.generated_output if final else None,
            "model_used": final.model_used if final else None,
            "input_tokens": final.prompt_tokens if final else None,
            "output_tokens": final.completion_tokens if final else None,
            "total_tokens": (final.prompt_tokens + final.completion_tokens)
            if final and final.prompt_tokens is not None and final.completion_tokens is not None else None,
            "cost_usd": final.cost_usd if final else None,
            "routing": final.meta if final else {},
        })
    return records


@router.get("/requests", summary="List this app's query request records")
async def list_query_requests(
    response: Response, page: int = QueryParam(1, ge=1),
    page_size: int = QueryParam(20, ge=1, le=100),
    status: Literal["completed", "failed", "processing", "cancelled", "rejected"] | None = None,
    db: AsyncSession = Depends(get_db), api_key: AccessAPIKey = Depends(get_current_api_key),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    filters = [Query.authorized_app_id == api_key.authorized_app_id]
    return await request_page(db, filters, page, page_size, status)


async def request_page(db, filters, page=1, page_size=20, status=None):
    if status:
        metadata = (cast(Query.meta_fields, JSON) if db.get_bind().dialect.name == "postgresql"
                    else type_coerce(Query.meta_fields, JSON))
        filters.append(metadata["status"].as_string() == status)
    total = (await db.execute(select(func.count()).select_from(Query).where(*filters))).scalar_one()
    rows = (await db.execute(select(Query).where(*filters).order_by(Query.id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"page": page, "page_size": page_size, "total": total,
            "items": await _request_records(db, rows)}


@router.get("/requests/{query_id}", summary="Get this app's query request and search results")
async def get_query_request(query_id: int, response: Response,
                            db: AsyncSession = Depends(get_db),
                            api_key: AccessAPIKey = Depends(get_current_api_key)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await request_detail(db, query_id, [Query.authorized_app_id == api_key.authorized_app_id])


async def request_detail(db, query_id, filters):
    row = (await db.execute(select(Query).where(Query.id == query_id,
                           *filters))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Query request not found")
    record = (await _request_records(db, [row]))[0]
    sources = (await db.execute(select(SearchResult).where(SearchResult.query_id == query_id)
                               .order_by(SearchResult.id))).scalars().all()
    record["sources"] = [json.loads(source.raw_response) for source in sources]
    return record
