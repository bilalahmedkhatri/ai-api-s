"""Pydantic v2 schemas for search engine clients and the query pipeline."""

from typing import Literal

from pydantic import BaseModel, Field

# ── Search aggregator ──────────────────────────────────────────────────────────

class UnifiedSearchResult(BaseModel):
    """Single normalised search hit — identical shape regardless of engine."""

    title: str
    url: str
    snippet: str
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    engine: str  # "tavily" | "brave" | "duckduckgo"


class UnifiedSearchResponse(BaseModel):
    query: str
    results: list[UnifiedSearchResult]
    engine: str


# ── Query pipeline ─────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)
    input_type: Literal["text", "voice", "image"] = "text"
    # Caller may force a specific search engine; omit for auto-selection.
    search_engine: Literal["tavily", "brave", "duckduckgo", "auto"] = "auto"


class RoutingDecision(BaseModel):
    """Internal routing metadata — also persisted to FinalResponse.meta_fields."""

    rule_matched: bool = False
    rule_pattern: str | None = None
    intent_category: str = "general"
    intent_score: float = 0.0
    requires_search: bool = True
    requires_llm: bool = True
    cache_hit: bool = False
    engine_used: str | None = None
    model_used: str | None = None
    elapsed_ms: float = 0.0


class QueryResponse(BaseModel):
    query_id: int
    answer: str
    routing: RoutingDecision
    sources: list[UnifiedSearchResult] = []
