"""tests/test_api_key_auth.py — Tests for AccessAPIKey generation, domain authorization, and REST endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from main import app
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.services.api_key_service import (
    generate_api_key,
    list_api_keys,
    revoke_api_key,
    verify_api_key,
)


@pytest.mark.asyncio
async def test_api_key_generation_and_verification():
    async with AsyncSessionLocal() as db:
        raw_key, record = await generate_api_key(
            db, client_name="Unit Test App", allowed_domain="unittest.com"
        )
        assert raw_key.startswith("gw_live_")
        assert record.client_name == "Unit Test App"
        assert record.allowed_domain == "unittest.com"
        assert record.is_active is True

        # Verification with valid key and domain
        verified = await verify_api_key(db, raw_key=raw_key, origin_domain="https://unittest.com/page")
        assert verified is not None
        assert verified.id == record.id
        assert verified.last_used_at is not None

        # Verification with domain mismatch
        mismatched = await verify_api_key(db, raw_key=raw_key, origin_domain="https://hacker.com")
        assert mismatched is None

        # Verification with wrong key
        wrong_key = await verify_api_key(db, raw_key="gw_live_invalidkey12345")
        assert wrong_key is None


@pytest.mark.asyncio
async def test_api_key_revocation():
    async with AsyncSessionLocal() as db:
        raw_key, record = await generate_api_key(db, client_name="Revoke App")
        assert record.is_active is True

        revoked_ok = await revoke_api_key(db, record.id)
        assert revoked_ok is True

        verified_after_revoke = await verify_api_key(db, raw_key=raw_key)
        assert verified_after_revoke is None


@pytest.mark.asyncio
async def test_keys_rest_endpoints(monkeypatch):
    secret = "test_cron_admin_secret"
    monkeypatch.setattr(settings, "cron_secret", secret)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        # Create key via POST /api/v1/keys/
        resp = await ac.post(
            "/api/v1/keys/",
            json={"client_name": "API Client App", "allowed_domain": "clientapp.com"},
            headers={"CRON_SECRET": secret},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "raw_key" in data
        assert data["raw_key"].startswith("gw_live_")
        assert data["client_name"] == "API Client App"
        created_id = data["id"]
        raw_key = data["raw_key"]

        # List keys via GET /api/v1/keys/
        list_resp = await ac.get(
            "/api/v1/keys/",
            headers={"CRON_SECRET": secret},
        )
        assert list_resp.status_code == 200
        keys_list = list_resp.json()
        assert any(k["id"] == created_id for k in keys_list)

        # Delete/revoke key via DELETE /api/v1/keys/{key_id}
        del_resp = await ac.delete(
            f"/api/v1/keys/{created_id}",
            headers={"CRON_SECRET": secret},
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_query_endpoint_api_key_enforcement(monkeypatch):
    async with AsyncSessionLocal() as db:
        raw_key, record = await generate_api_key(db, client_name="Query Test Client")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        # Test 1: REQUIRE_API_KEY_FOR_QUERY = True, but no key provided -> 401
        monkeypatch.setattr(settings, "require_api_key_for_query", True)
        resp_no_key = await ac.post(
            "/api/v1/query/",
            json={"query": "Hello world", "input_type": "text"},
        )
        assert resp_no_key.status_code == 401

        # Test 2: REQUIRE_API_KEY_FOR_QUERY = True, valid X-API-Key header provided -> 200
        resp_with_key = await ac.post(
            "/api/v1/query/",
            json={"query": "Hello world", "input_type": "text"},
            headers={"X-API-Key": raw_key},
        )
        assert resp_with_key.status_code == 200

        # Test 3: REQUIRE_API_KEY_FOR_QUERY = False (default), no key provided -> 200
        monkeypatch.setattr(settings, "require_api_key_for_query", False)
        resp_dev = await ac.post(
            "/api/v1/query/",
            json={"query": "Hello world", "input_type": "text"},
        )
        assert resp_dev.status_code == 200
