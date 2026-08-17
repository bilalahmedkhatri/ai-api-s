"""tests/test_cron_endpoint.py — Unit test for HTTP cron trigger endpoint."""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_sync_models_cron_endpoint_dry_run():
    response = client.post("/api/v1/cron/sync-models?dry_run=true&max_paid=5")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run"
    assert "total_fetched" in data
    assert "free_models_count" in data
    assert data["paid_models_selected"] <= 5
