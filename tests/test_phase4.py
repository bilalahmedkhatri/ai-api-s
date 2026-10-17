"""Phase 4 unit tests — no network calls, no API keys, no Upstash needed."""

import json
import zlib

import pytest

# ── Cache compression tests ────────────────────────────────────────────────────
from app.core.cache import _COMPRESS_PREFIX, TTL_DYNAMIC, TTL_STATIC, _compress, _decompress


def test_ttl_values():
    assert TTL_STATIC == 60 * 60 * 24 * 7   # 7 days
    assert TTL_DYNAMIC == 60 * 60             # 1 hour
    assert TTL_STATIC > TTL_DYNAMIC


def test_compress_roundtrip_small():
    """Small payloads are stored uncompressed."""
    data = "hello world"
    compressed = _compress(data)
    assert _decompress(compressed) == data
    assert compressed[:2] != _COMPRESS_PREFIX  # no compression for small data


def test_compress_roundtrip_large():
    """Large payloads are zlib-compressed."""
    data = "x" * 1024  # > 512-byte threshold
    compressed = _compress(data)
    assert compressed[:2] == _COMPRESS_PREFIX  # compression applied
    assert _decompress(compressed) == data
    assert len(compressed) < len(data.encode())  # actually smaller


def test_compress_json_payload():
    """Realistic LLM response payload roundtrips correctly."""
    payload = {"answer": "A" * 800, "model": "llama-3.1-70b", "tokens": 512}
    data = json.dumps(payload)
    assert json.loads(_decompress(_compress(data))) == payload


from app.services.model_router import LLM_TIMEOUT


def test_llm_timeout_reasonable():
    assert 10 <= LLM_TIMEOUT <= 30  # sanity check: between 10s and 30s


# ── Dispatcher cache TTL selection ─────────────────────────────────────────────

from app.services.dispatcher import _cache_ttl, _md5


def test_static_intents_get_long_ttl():
    for intent in ["coding", "mathematics", "general_knowledge"]:
        assert _cache_ttl(intent) == TTL_STATIC


def test_dynamic_intents_get_short_ttl():
    for intent in ["general", "weather", "news", "creative_writing"]:
        assert _cache_ttl(intent) == TTL_DYNAMIC


def test_md5_normalises_whitespace_and_case():
    assert _md5("  Hello World  ") == _md5("hello world")


# ── Query endpoint cache-control header test ───────────────────────────────────

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_query_route_registered():
    resp = client.get("/openapi.json")
    assert "/api/v1/query/" in resp.json()["paths"]


def test_query_requires_body():
    resp = client.post("/api/v1/query/", json={})
    assert resp.status_code == 422
