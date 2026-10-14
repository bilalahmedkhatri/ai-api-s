"""Keep regression tests off the configured production database and providers."""

import io
import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.auth import get_current_api_key
from app.db.database import get_db
from app.models.db_models import Base
from fastapi import Request
from main import app
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.test_app_authorization import AsyncAdapter
from tests.test_app_authorization import isolated as onboarding_db  # noqa: F401


@pytest.fixture
def tmp_path():
    # Python 3.14's Windows mode=0700 used by pytest's default temporary
    # directory blocks this workspace sandbox. Inherit workspace permissions.
    root = Path(__file__).resolve().parent
    path = root / (".test-artifacts-" + uuid.uuid4().hex)
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        if path.resolve().parent != root:
            raise RuntimeError("Test artifact path escaped the workspace")
        shutil.rmtree(path)


@pytest.fixture
def unit_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    yield AsyncAdapter(session)
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def isolate_regression_dependencies(request, monkeypatch):
    name = request.path.name
    if name == "test_app_authorization.py":
        yield
        return
    previous = dict(app.dependency_overrides)
    db = request.getfixturevalue("unit_db")

    async def isolated_db():
        yield db

    app.dependency_overrides[get_db] = isolated_db
    if name in {"test_ingest_routes.py", "test_tts_audio.py", "test_phase3.py", "test_phase4.py"}:
        # These test handler validation/provider behavior. Real authorization is
        # exercised separately, with no auth overrides, in test_app_authorization.
        async def handler_auth(request: Request):
            request.state.authorized_app = SimpleNamespace(id=1, owner_user_id=1)
            return SimpleNamespace(id=1, authorized_app_id=1)
        app.dependency_overrides[get_current_api_key] = handler_auth
    if name == "test_model_usage.py":
        monkeypatch.setattr(request.module, "AsyncSessionLocal", lambda: db)
        monkeypatch.setattr(request.module, "init_db", AsyncMock())
    if name == "test_phase5.py":
        from app.api.v1.health import ComponentHealth
        monkeypatch.setattr("app.api.v1.health._check_upstash", AsyncMock(return_value=ComponentHealth(status="degraded")))
    if name in {"test_provider_cron.py", "test_cron_endpoint.py"}:
        def fake_urlopen(req, **kwargs):
            data = {"data": [
                {"id": "test-free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "test-paid", "pricing": {"prompt": "0.001", "completion": "0.002"}},
            ], "models": [{"name": "test-command"}]}
            response = io.BytesIO(json.dumps(data).encode())
            response.status = 200
            return response
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("app.services.provider_models_sync.init_db", AsyncMock())
        monkeypatch.setattr("app.services.openrouter_sync.init_db", AsyncMock())
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
