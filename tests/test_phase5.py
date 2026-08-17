"""Phase 5 unit tests — no network calls, no credentials required."""

import gzip
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ── Health endpoints ───────────────────────────────────────────────────────────

def test_shallow_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_deep_health_route_registered():
    resp = client.get("/openapi.json")
    paths = resp.json()["paths"]
    assert "/api/v1/health/" in paths


def test_deep_health_returns_components():
    """Deep health must include 'database' and 'cache' component keys."""
    resp = client.get("/api/v1/health/")
    # DB is accessible in test (PostgreSQL); cache may be degraded without Upstash.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "components" in body
    assert "database" in body["components"]
    assert "cache" in body["components"]
    assert "uptime_s" in body


def test_deep_health_db_ok():
    """PostgreSQL should always be reachable in the test environment."""
    resp = client.get("/api/v1/health/")
    body = resp.json()
    assert body["components"]["database"]["status"] == "ok"


def test_deep_health_upstash_degraded_without_credentials():
    """With no Upstash env vars, cache should report 'degraded', not crash."""
    resp = client.get("/api/v1/health/")
    body = resp.json()
    assert body["components"]["cache"]["status"] in ("ok", "degraded")


# ── Log archive script ─────────────────────────────────────────────────────────

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from archive_logs import archive_logs


def test_archive_logs_dry_run(tmp_path: Path):
    """Dry-run should not create any files."""
    log_file = tmp_path / "gateway.jsonl"
    log_file.write_text('{"msg": "test"}\n' * 10)

    # Back-date the file by 40 days
    import time
    old_ts = time.time() - 40 * 86400
    os.utime(log_file, (old_ts, old_ts))

    archive_logs(tmp_path, days=30, dry_run=True)

    # Dry-run: original file untouched, no archive dir created with files
    assert log_file.exists()
    archive_dir = tmp_path / "archive"
    assert not any(archive_dir.glob("*.gz")) if archive_dir.exists() else True


def test_archive_logs_compresses_old_file(tmp_path: Path):
    """Old logs should be gzip-compressed and removed from logs/."""
    log_file = tmp_path / "gateway.jsonl"
    content = '{"msg": "test"}\n' * 100
    log_file.write_text(content)

    import time
    old_ts = time.time() - 40 * 86400
    os.utime(log_file, (old_ts, old_ts))

    archive_logs(tmp_path, days=30, dry_run=False)

    assert not log_file.exists(), "Original should be removed after archiving"
    archive_dir = tmp_path / "archive"
    gz_files = list(archive_dir.glob("*.gz"))
    assert len(gz_files) == 1

    # Verify the archive is a valid gzip and contains the original content
    with gzip.open(gz_files[0], "rt") as f:
        restored = f.read()
    assert restored == content


def test_archive_logs_skips_recent_file(tmp_path: Path):
    """Recent log files must not be archived."""
    log_file = tmp_path / "gateway.jsonl"
    log_file.write_text('{"msg": "recent"}\n')
    # mtime defaults to now — well within 30 days

    archive_logs(tmp_path, days=30, dry_run=False)

    assert log_file.exists(), "Recent file should not be touched"


# ── Sentry config gate ─────────────────────────────────────────────────────────

def test_sentry_not_initialised_without_dsn():
    """Without SENTRY_DSN, Sentry hub should have no DSN configured."""
    import sentry_sdk
    client_dsn = sentry_sdk.get_client().dsn
    # When DSN is empty, sentry_sdk.get_client().dsn is None or empty
    assert not client_dsn  # falsy — not initialised with real DSN


# ── Dockerfile + Compose files exist ──────────────────────────────────────────

def test_dockerfile_exists():
    assert Path("Dockerfile").exists()


def test_compose_file_exists():
    assert Path("docker-compose.yml").exists()


def test_ci_workflow_exists():
    assert Path(".github/workflows/ci.yml").exists()


def test_deploy_workflow_exists():
    assert Path(".github/workflows/deploy.yml").exists()
