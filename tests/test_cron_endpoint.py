"""tests/test_cron_endpoint.py — Unit test for HTTP cron trigger endpoint."""

import pytest
from fastapi.testclient import TestClient
from main import app
from app.core.config import settings

client = TestClient(app)


def test_sync_models_cron_endpoint_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", None)
    response = client.post("/api/v1/cron/sync-models?dry_run=true&max_paid=5")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run"
    assert "total_fetched" in data
    assert "free_models_count" in data
    assert data["paid_models_selected"] <= 5


def test_sync_models_cron_endpoint_secret_auth(monkeypatch):
    test_token = "test_secret_123"
    monkeypatch.setattr(settings, "cron_secret", test_token)

    # Unauthorized request without secret
    unauth_resp = client.post("/api/v1/cron/sync-models?dry_run=true")
    assert unauth_resp.status_code == 401

    # Authorized request with CRON_SECRET header (Upstash QStash style)
    auth_resp = client.post(
        "/api/v1/cron/sync-models?dry_run=true&max_paid=5",
        headers={"CRON_SECRET": test_token},
    )
    assert auth_resp.status_code == 200
    assert auth_resp.json()["status"] == "dry_run"
