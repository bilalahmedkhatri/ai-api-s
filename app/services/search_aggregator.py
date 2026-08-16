"""Async search engine clients: Tavily, Brave, DuckDuckGo.

All three return a UnifiedSearchResponse so the rest of the pipeline
never cares which engine was used.
"""

import logging

import httpx

from app.api.v1.query.schemas import UnifiedSearchResponse, UnifiedSearchResult
from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


# ── Tavily ─────────────────────────────────────────────────────────────────────

async def search_tavily(query: str, max_results: int = 5) -> UnifiedSearchResponse:
    """Tavily AI-focused search — best for multi-hop factual queries."""
    api_key = getattr(settings, "tavily_api_key", "")
    if not api_key:
        raise ValueError("TAVILY_API_KEY not set in environment")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
        )
        resp.raise_for_status()
        data = resp.json()

    results = [
        UnifiedSearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", "")[:500],
            relevance_score=float(r.get("score", 1.0)),
            engine="tavily",
        )
        for r in data.get("results", [])
    ]
    logger.info("Tavily returned %d results for query=%r", len(results), query[:60])
    return UnifiedSearchResponse(query=query, results=results, engine="tavily")


# ── Brave ──────────────────────────────────────────────────────────────────────

async def search_brave(query: str, max_results: int = 5) -> UnifiedSearchResponse:
    """Brave Search — general web + news aggregation."""
    api_key = getattr(settings, "brave_api_key", "")
    if not api_key:
        raise ValueError("BRAVE_API_KEY not set in environment")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    web_hits = data.get("web", {}).get("results", [])
    results = [
        UnifiedSearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("description", "")[:500],
            relevance_score=1.0,  # Brave doesn't expose scores
            engine="brave",
        )
        for r in web_hits
    ]
    logger.info("Brave returned %d results for query=%r", len(results), query[:60])
    return UnifiedSearchResponse(query=query, results=results, engine="brave")


# ── DuckDuckGo (fallback, no API key) ─────────────────────────────────────────

async def search_duckduckgo(query: str, max_results: int = 5) -> UnifiedSearchResponse:
    """DuckDuckGo — free fallback, no API key required."""
    # DDGS.text() is synchronous; run in executor to stay non-blocking.
    import asyncio

    from duckduckgo_search import DDGS  # lazy import; only loads when called

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None,
        lambda: list(DDGS().text(query, max_results=max_results)),
    )

    results = [
        UnifiedSearchResult(
            title=r.get("title", ""),
            url=r.get("href", ""),
            snippet=r.get("body", "")[:500],
            relevance_score=1.0,
            engine="duckduckgo",
        )
        for r in raw
    ]
    logger.info("DuckDuckGo returned %d results for query=%r", len(results), query[:60])
    return UnifiedSearchResponse(query=query, results=results, engine="duckduckgo")


# ── Auto-selector ──────────────────────────────────────────────────────────────

async def search(query: str, engine: str = "auto", max_results: int = 5) -> UnifiedSearchResponse:
    """Select engine and fall back gracefully: Tavily → Brave → DDG."""
    has_tavily = bool(getattr(settings, "tavily_api_key", ""))
    has_brave = bool(getattr(settings, "brave_api_key", ""))

    order: list[str]
    if engine == "tavily":
        order = ["tavily"]
    elif engine == "brave":
        order = ["brave"]
    elif engine == "duckduckgo":
        order = ["duckduckgo"]
    else:  # auto: prefer key-authenticated engines
        order = (
            ["tavily", "brave", "duckduckgo"] if has_tavily
            else ["brave", "duckduckgo"] if has_brave
            else ["duckduckgo"]
        )

    last_exc: Exception | None = None
    for eng in order:
        try:
            if eng == "tavily":
                return await search_tavily(query, max_results)
            elif eng == "brave":
                return await search_brave(query, max_results)
            else:
                return await search_duckduckgo(query, max_results)
        except Exception as exc:
            logger.warning("Engine %s failed: %s — trying next", eng, exc)
            last_exc = exc

    raise RuntimeError(f"All search engines failed. Last error: {last_exc}")
