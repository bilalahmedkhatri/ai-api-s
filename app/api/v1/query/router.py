"""POST /api/v1/query — main entry point for the AI gateway pipeline."""

import logging

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.query.schemas import QueryRequest, QueryResponse
from app.core.auth import get_current_api_key
from app.db.database import get_db
from app.models.db_models import AccessAPIKey
from app.services.dispatcher import process_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["query"])


@router.post("/", response_model=QueryResponse, summary="Process a query through the AI gateway")
async def query_endpoint(
    req: QueryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: AccessAPIKey | None = Depends(get_current_api_key),
    cache_control: str | None = Header(default=None),
) -> QueryResponse:
    """
    Full pipeline:
    1. Third-party AccessAPIKey validation & domain check
    2. Upstash Redis cache lookup (bypass with Cache-Control: no-cache)
    3. Rule engine (SQLite 1hr cache + regex fast path)
    4. Semantic router (cosine similarity → 30B+ model selection)
    5. Search aggregation (Tavily / Brave / DDG)
    6. RAG prompt + 30B+ LLM with automatic fallbacks
    7. DB persistence (tokens, cost, telemetry)
    8. Write result to Upstash with dynamic TTL
    """
    no_cache = cache_control is not None and "no-cache" in cache_control.lower()

    result = await process_query(req, db, no_cache=no_cache)

    logger.info(
        "Query complete",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "query_id": result.query_id,
            "api_key_client": api_key.client_name if api_key else "anonymous",
            "cache_hit": result.routing.cache_hit,
            "elapsed_ms": result.routing.elapsed_ms,
        },
    )
    return result
