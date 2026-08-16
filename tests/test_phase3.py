"""Phase 3 unit tests — no network calls, no LLM keys required."""

import asyncio
import hashlib
import re

import pytest

# ── Rule engine tests ──────────────────────────────────────────────────────────
from app.services.rule_engine import _RULES, RuleResult, _md5


def test_md5_deterministic():
    assert _md5("Hello World") == _md5("hello world")


def test_weather_rule_matches():
    pattern, intent, requires_llm, requires_search = _RULES[0]
    assert pattern.search("weather in London tomorrow")
    assert intent == "weather"
    assert not requires_llm
    assert requires_search


def test_greeting_rule_matches():
    greeting_rule = next((r for r in _RULES if r[1] == "greeting"), None)
    assert greeting_rule is not None
    assert greeting_rule[0].search("Hello!")
    assert not greeting_rule[2]   # requires_llm = False
    assert not greeting_rule[3]   # requires_search = False


def test_math_rule_matches():
    math_rule = next((r for r in _RULES if r[1] == "math_expression"), None)
    assert math_rule is not None
    assert math_rule[0].search("what is 42 + 58")


def test_no_rule_match():
    test_query = "explain quantum entanglement in simple terms"
    matched = any(r[0].search(test_query) for r in _RULES)
    assert not matched  # should fall through to semantic router


# ── Unified search schema tests ────────────────────────────────────────────────

from app.api.v1.query.schemas import (
    QueryRequest,
    QueryResponse,
    RoutingDecision,
    UnifiedSearchResponse,
    UnifiedSearchResult,
)


def test_unified_search_result_defaults():
    r = UnifiedSearchResult(title="T", url="https://x.com", snippet="S", engine="brave")
    assert r.relevance_score == 1.0


def test_query_request_defaults():
    req = QueryRequest(query="tell me about black holes")
    assert req.input_type == "text"
    assert req.search_engine == "auto"


def test_query_request_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        QueryRequest(query="")  # min_length=1 should fail


def test_routing_decision_defaults():
    rd = RoutingDecision()
    assert not rd.cache_hit
    assert not rd.rule_matched
    assert rd.intent_category == "general"


# ── Semantic router cosine math ────────────────────────────────────────────────

import numpy as np
from app.services.semantic_router import _cosine


def test_cosine_identical_vectors():
    v = np.array([1.0, 0.0, 0.0])
    assert abs(_cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors():
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert abs(_cosine(a, b)) < 1e-6


def test_cosine_zero_vector():
    a = np.array([0.0, 0.0])
    b = np.array([1.0, 0.0])
    assert _cosine(a, b) == 0.0


# ── Query route registration ───────────────────────────────────────────────────

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_query_route_in_openapi():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/query/" in resp.json()["paths"]


def test_query_endpoint_missing_body():
    resp = client.post("/api/v1/query/", json={})
    assert resp.status_code == 422  # missing required 'query' field
