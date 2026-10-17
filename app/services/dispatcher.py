"""Central dispatcher — orchestrates the full query pipeline.

Flow:
  1. Upstash cache lookup  (MD5 hash, Cache-Control: no-cache bypass)
  2. Persist raw query → queries table
  3. Rule engine          (SQLite 1hr cache + regex fast path)
  4. Semantic router      (cosine similarity, if rule engine misses)
  5. Search aggregation   (Tavily / Brave / DDG if requires_search)
  6. Async LLM via model_router (active DB chat models + provider keys + timeout)
  7. Persist FinalResponse (tokens, cost, telemetry)
  8. Write answer to Upstash cache with dynamic TTL
"""

import hashlib
import json
import logging
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.query.schemas import (
    QueryRequest,
    QueryResponse,
    RoutingDecision,
    UnifiedSearchResult,
)
from app.core.cache import TTL_DYNAMIC, TTL_STATIC, cache
from app.models.db_models import FinalResponse, Query, SearchResult
from app.services.model_router import ModelRoutingError, call_llm
from app.services.rule_engine import is_time_query, run_rule_engine
from app.services.search_aggregator import search
from app.services.semantic_router import classify

logger = logging.getLogger(__name__)

# Intent categories where the answer is considered "static" (long TTL)
_STATIC_INTENTS = {"general_knowledge", "mathematics", "coding", "math_task", "math_expression"}


def _md5(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def _cache_ttl(intent: str) -> int:
    """Return TTL_STATIC for factual answers, TTL_DYNAMIC for search-backed ones."""
    return TTL_STATIC if intent in _STATIC_INTENTS else TTL_DYNAMIC


def _build_rag_prompt(query: str, results: list[UnifiedSearchResult]) -> str:
    """Append search snippets to the query as a RAG context block."""
    if not results:
        return query
    ctx = "\n".join(
        f"[{i+1}] {r.title}\n{r.snippet}\nSource: {r.url}"
        for i, r in enumerate(results[:5])
    )
    return (
        "Answer the following question using the provided web search results.\n\n"
        f"Question: {query}\n\n"
        f"Search Results:\n{ctx}\n\nAnswer:"
    )


async def process_query(
    req: QueryRequest,
    db: AsyncSession,
    no_cache: bool = False,
    query_row: Query | None = None,
) -> QueryResponse:
    """
    Full pipeline entry point.

    Args:
        req:       Validated QueryRequest from the endpoint.
        db:        Async SQLAlchemy session (injected by FastAPI Depends).
        no_cache:  True when caller sent Cache-Control: no-cache header.
    """
    t0 = time.monotonic()
    no_cache = no_cache or is_time_query(req.query)
    routing = RoutingDecision()
    q_row = query_row or Query(raw_query=req.query, input_type=req.input_type)
    if query_row is None:
        db.add(q_row)
        await db.flush()
    query_id = q_row.id
    cache_key = f"gw:v2:{q_row.authorized_app_id}:{_md5(req.query)}"

    # ── 1. Upstash cache lookup ────────────────────────────────────────────────
    if not no_cache:
        cached_payload = await cache.get(cache_key)
        if cached_payload and not cached_payload.get("answer", "").startswith(("[LLM error:", "[Rule-matched:")):
            routing.cache_hit = True
            routing.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            routing.intent_category = cached_payload.get("intent", "cached")
            routing.model_used = cached_payload.get("model_used")
            routing.engine_used = cached_payload.get("engine_used")
            sources = [UnifiedSearchResult(**s) for s in cached_payload.get("sources", [])]
            # Still persist a Query row for audit trail
            routing.requires_llm = routing.requires_search = False
            await _persist_final(db, query_id, cached_payload["answer"], routing.model_used or "cache",
                                 routing, req.query, prompt_tokens=0, completion_tokens=0, cost_usd=0, sources=sources)
            logger.info("Upstash cache hit", extra={"cache_hit": True, "elapsed_ms": routing.elapsed_ms})
            return QueryResponse(
                query_id=q_row.id,
                answer=cached_payload["answer"],
                routing=routing,
                sources=sources,
            )

    # ── 2. Persist raw query ───────────────────────────────────────────────────
    # ── 3. Rule engine (fast path) ─────────────────────────────────────────────
    rule = await run_rule_engine(req.query, db, no_cache=no_cache, authorized_app_id=q_row.authorized_app_id)
    sources: list[UnifiedSearchResult] = []

    if rule.cache_hit and rule.cached_answer:
        routing.cache_hit = True
        routing.rule_matched = True
        routing.rule_pattern = "sqlite_cache_hit"
        routing.intent_category = rule.cached_intent or "cached"
        routing.engine_used = rule.cached_engine
        routing.requires_llm = False
        routing.requires_search = False
        routing.model_used = rule.cached_model
        saved_sources = (await db.execute(select(SearchResult).where(
            SearchResult.query_id == rule.cached_query_id
        ).order_by(SearchResult.id))).scalars().all()
        sources = [UnifiedSearchResult(**json.loads(s.raw_response)) for s in saved_sources]
        routing.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        await _persist_final(db, query_id, rule.cached_answer, rule.cached_model or "sqlite_cache", routing, req.query,
                             prompt_tokens=0, completion_tokens=0, cost_usd=0, sources=sources)
        return QueryResponse(query_id=query_id, answer=rule.cached_answer, routing=routing, sources=sources)

    if rule.matched:
        routing.rule_matched = True
        routing.rule_pattern = rule.pattern
        routing.intent_category = rule.intent
        routing.requires_llm = rule.requires_llm
        routing.requires_search = rule.requires_search
    else:
        # ── 4. Semantic router ─────────────────────────────────────────────────
        stage_started = time.monotonic()
        sem = await classify(req.query)
        routing.stage_durations_ms["semantic"] = round((time.monotonic() - stage_started) * 1000, 1)
        routing.intent_category = sem.category
        routing.intent_score = sem.score
        routing.model_used = sem.model_hint

    # ── 5. Search aggregation ──────────────────────────────────────────────────
    if routing.requires_search:
        stage_started = time.monotonic()
        try:
            search_resp = await search(req.query, engine=req.search_engine, max_results=5)
            sources = search_resp.results
            routing.engine_used = search_resp.engine
        except Exception as exc:
            logger.warning("Search failed, continuing without results: %s", exc)
        routing.stage_durations_ms["search"] = round((time.monotonic() - stage_started) * 1000, 1)

    # ── 6. LLM call via DB-backed model_router ────────────────────────────────
    answer = ""
    prompt_tokens = completion_tokens = 0
    cost_usd = 0.0
    search_unavailable = rule.matched and rule.requires_search and not sources
    routing.search_unavailable = search_unavailable
    if search_unavailable:
        routing.requires_llm = False

    if routing.requires_llm:
        prompt = _build_rag_prompt(req.query, sources)
        try:
            llm_result = await call_llm(
                prompt=prompt,
                intent=routing.intent_category,
                db=db,
            )
            answer = llm_result.answer
            routing.model_used = llm_result.model_used
            routing.model_attempts = llm_result.attempts
            routing.stage_durations_ms["llm"] = llm_result.elapsed_ms
            prompt_tokens = llm_result.prompt_tokens
            completion_tokens = llm_result.completion_tokens
            cost_usd = llm_result.cost_usd
        except ModelRoutingError as exc:
            routing.model_used = None
            routing.model_attempts = exc.attempts
            routing.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            await _persist_final(db, query_id, "[LLM error: No available chat model]",
                                 "failed", routing, req.query, sources=sources)
            raise HTTPException(503, "AI generation is temporarily unavailable") from None
    else:
        answer = ("I couldn't retrieve current search results. Please try again."
                  if search_unavailable else rule.answer or "Please provide a question I can help with.")

    # ── 7. Persist FinalResponse with full telemetry ───────────────────────────
    routing.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    await _persist_final(
        db, query_id, answer, routing.model_used or "rule",
        routing, req.query,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        sources=sources,
    )

    # ── 8. Write to Upstash cache ──────────────────────────────────────────────
    if not search_unavailable and routing.intent_category != "time_lookup":
        ttl = _cache_ttl(routing.intent_category)
        payload = {
            "answer": answer,
            "intent": routing.intent_category,
            "model_used": routing.model_used,
            "engine_used": routing.engine_used,
            "sources": [s.model_dump() for s in sources],
        }
        await cache.set(cache_key, payload, ttl=ttl)

    logger.info(
        "Query processed",
        extra={
            "input_type": req.input_type,
            "cache_hit": routing.cache_hit,
            "intent": routing.intent_category,
            "engine": routing.engine_used,
            "model": routing.model_used,
            "elapsed_ms": routing.elapsed_ms,
        },
    )

    return QueryResponse(query_id=query_id, answer=answer, routing=routing, sources=sources)


async def _persist_final(
    db: AsyncSession,
    query_id: int,
    answer: str,
    model: str,
    routing: RoutingDecision,
    raw_query: str,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: float | None = None,
    sources: list[UnifiedSearchResult] | None = None,
) -> None:
    """Write FinalResponse row with full telemetry in meta_fields."""
    meta = routing.model_dump()
    meta["query_hash"] = _md5(raw_query)
    meta["status"] = "failed" if model == "failed" else "completed"

    row = FinalResponse(
        query_id=query_id,
        generated_output=answer,
        model_used=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        meta_fields=json.dumps(meta),
    )
    db.add(row)
    for source in sources or []:
        db.add(SearchResult(query_id=query_id, engine_name=source.engine,
                            raw_response=json.dumps(source.model_dump())))
    await db.commit()
