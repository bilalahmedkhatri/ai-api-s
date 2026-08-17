"""tests/test_provider_cron.py — Tests for Groq, Cohere, NVIDIA provider model sync and cron endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from main import app
from app.core.config import settings
from app.services.provider_models_sync import (
    fetch_groq_models,
    fetch_cohere_models,
    fetch_nvidia_models,
    run_provider_models_sync,
)


def test_fetch_groq_models():
    models = fetch_groq_models()
    assert len(models) > 0
    for m in models:
        assert m["provider"] == "groq"
        assert m["name"].startswith("groq/")


def test_fetch_cohere_models():
    models = fetch_cohere_models()
    assert len(models) > 0
    for m in models:
        assert m["provider"] == "cohere"
        assert m["name"].startswith("cohere/")


def test_fetch_nvidia_models():
    models = fetch_nvidia_models()
    assert len(models) > 0
    for m in models:
        assert m["provider"] == "nvidia"
        assert m["name"].startswith("nvidia/")


@pytest.mark.asyncio
async def test_run_provider_models_sync_dry_run():
    res = await run_provider_models_sync(dry_run=True)
    assert res["status"] == "dry_run"
    assert "groq" in res["providers_synced"]
    assert "cohere" in res["providers_synced"]
    assert "nvidia" in res["providers_synced"]
    assert res["total_fetched"] > 0


@pytest.mark.asyncio
async def test_sync_provider_models_cron_endpoint_unauthorized(monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "super_secret_test_key_123")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        resp = await ac.post("/api/v1/cron/sync-provider-models")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sync_provider_models_cron_endpoint_authorized(monkeypatch):
    secret = "super_secret_test_key_123"
    monkeypatch.setattr(settings, "cron_secret", secret)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        resp = await ac.post(
            "/api/v1/cron/sync-provider-models?dry_run=true",
            headers={"CRON_SECRET": secret},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dry_run"
        assert data["total_fetched"] > 0


@pytest.mark.asyncio
async def test_sync_other_models_alias_cron_endpoint(monkeypatch):
    secret = "super_secret_test_key_123"
    monkeypatch.setattr(settings, "cron_secret", secret)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        resp = await ac.post(
            "/api/v1/cron/sync-other-models?dry_run=true",
            headers={"CRON_SECRET": secret},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dry_run"
