# ruff: noqa: F811
"""Shared endpoint schema, compatibility and provider routing in isolated SQL."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models.db_models import (
    AIModel,
    Base,
    MediaModel,
    ModelEndpoint,
    ProviderAPIKey,
    TTSModel,
    TTSVoice,
)
from app.services import media_gen_service, model_router
from app.services.model_endpoints import legacy_views
from app.services.tts import catalog, generator
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import Session

from tests.test_dashboard import dashboard  # noqa: F401
from tests.test_query_async import chat_db  # noqa: F401

URL = "/api/v1/dashboard/model-endpoints"


def test_migration_preserves_sources_voices_keys_and_prices():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/e610a2c947bf_unified_model_endpoints.py"
    spec = importlib.util.spec_from_file_location("endpoint_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name != "model_endpoints"])
    try:
        with Session(engine) as session:
            session.add(AIModel(name="tts-test", provider="gemini", prompt_price_per_1k=2, is_free=False))
            tts = TTSModel(name="tts-test", provider="gemini", api_endpoint_url="https://tts.test/speak",
                           default_voice="Kore", provider_config={"model": "tts-test"})
            media = MediaModel(name="image-test", provider="openai", media_type="image",
                               api_base_url="https://image.test", api_endpoint_path="/v1/generate",
                               is_async_poll=False, default_params={"quality": "high"})
            session.add_all([tts, media, ProviderAPIKey(provider="gemini", label="test", encrypted_key="unchanged-secret")])
            session.flush()
            session.add(TTSVoice(model_id=tts.id, voice_name="Kore"))
            session.commit()
            tts_id, media_id = tts.id, media.id
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        with Session(engine) as session:
            endpoints = session.scalars(select(ModelEndpoint).order_by(ModelEndpoint.id)).all()
            assert len(endpoints) == 2
            assert endpoints[0].legacy_tts_model_id == tts_id
            assert endpoints[0].ai_model.prompt_price_per_1k == 2
            assert endpoints[0].config["provider_config"] == {"model": "tts-test"}
            assert endpoints[0].created_at is not None and endpoints[0].updated_at is not None
            assert endpoints[1].endpoint_url == "https://image.test/v1/generate"
            assert endpoints[1].config["default_params"] == {"quality": "high"}
            assert session.get(TTSModel, tts_id).api_endpoint_url == "https://tts.test/speak"
            assert session.get(MediaModel, media_id).api_endpoint_path == "/v1/generate"
            assert session.scalars(select(TTSVoice)).one().model_id == tts_id
            assert endpoints[0].ai_model.api_keys[0].encrypted_key == "unchanged-secret"
        columns = {column["name"] for column in inspect(engine).get_columns("model_endpoints")}
        assert not {"name", "provider", "encrypted_key", "prompt_price_per_1k"} & columns
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
        assert {"ai_models", "tts_models", "media_models", "provider_api_keys", "tts_voices"} <= set(inspect(engine).get_table_names())
        with Session(engine) as session:
            assert len(session.scalars(select(AIModel)).all()) == 2
    finally:
        engine.dispose()


async def test_admin_crud_timestamps_legacy_identity_and_disable(dashboard):
    db, member, headers = dashboard
    model = AIModel(name="shared-tts", provider="gemini")
    db.add(model)
    await db.commit()
    model_id = model.id
    body = {"ai_model_id": model_id, "model_type": "tts", "adapter": "http",
            "endpoint_url": "https://tts.test/generate", "config": {"default_voice": "Kore", "provider_config": {"model": "shared-tts"}}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, headers=headers, json=body)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["created_at"] and data["updated_at"]
        assert not {"name", "provider", "pricing", "encrypted_key"} & data.keys()
        identifier, legacy_id = data["id"], data["legacy_tts_model_id"]
        assert legacy_id is not None
        voice = TTSVoice(model_id=legacy_id, voice_name="Kore")
        db.add(voice)
        await db.commit()
        assert (await client.post(URL, headers=headers, json=body)).status_code == 409
        endpoint = await db.get(ModelEndpoint, identifier)
        endpoint.updated_at = datetime(2000, 1, 1, tzinfo=UTC)
        await db.commit()
        response = await client.put(f"{URL}/{identifier}", headers=headers,
                                    json={**body, "endpoint_url": "https://tts.test/new"})
        assert response.status_code == 200
        assert response.json()["created_at"] == data["created_at"]
        assert response.json()["updated_at"] > "2000"
        legacy = await db.get(TTSModel, legacy_id)
        views = await legacy_views(db, [legacy], "tts")
        assert views[0].id == legacy_id and views[0].api_endpoint_url == "https://tts.test/new"
        assert legacy.api_endpoint_url is None  # No ORM mutation of old source settings.
        assert (await catalog.get_dynamic_voices("shared-tts", db))[1] == 1
        response = await client.put(f"{URL}/{identifier}", headers=headers, json={**body, "is_active": False})
        assert response.status_code == 200
        assert await catalog.get_dynamic_tts_models(db) == []
        assert (await client.get(f"{URL}?ai_model_id={model_id}", headers=headers)).json()["endpoints"][0]["id"] == identifier
        member.role = "viewer"
        await db.commit()
        assert (await client.get(URL, headers=headers)).status_code == 403
        assert (await client.put(f"{URL}/{identifier}", headers=headers, json=body)).status_code == 403
        assert (await client.post(URL, headers=headers, json=body)).status_code == 403
        assert (await client.get(URL)).status_code == 401


async def test_endpoint_validation_omits_credentials_and_rejects_reassignment(dashboard):
    db, _, headers = dashboard
    model = AIModel(name="endpoint-chat", provider="openai")
    db.add(model)
    await db.commit()
    body = {"ai_model_id": model.id, "model_type": "chat", "adapter": "litellm",
            "api_base_url": "https://provider.test/v1", "endpoint_url": "https://provider.test/v1/chat/completions"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, headers=headers, json=body)
        assert response.status_code == 201
        identifier = response.json()["id"]
        assert (await client.post(URL, headers={**headers, "Origin": "https://foreign.test"}, json=body)).status_code == 403
        for change in ({"api_base_url": "https://fake-secret@provider.test"}, {"endpoint_url": "https://provider.test?key=fake-secret"},
                       {"config": {"api_key": "fake-secret"}}, {"config": {"name": "duplicate"}},
                       {"api_base_url": None}, {"adapter": "http"}):
            response = await client.post(URL, headers=headers, json={**body, **change})
            assert response.status_code == 422 and "fake-secret" not in response.text
        assert (await client.put(f"{URL}/{identifier}", headers=headers, json={**body, "model_type": "embedding"})).status_code == 409
        assert (await client.post(URL, headers=headers, json={**body, "ai_model_id": 9999})).status_code == 404


async def test_chat_uses_shared_url_and_provider_pool_but_not_other_modalities(chat_db, monkeypatch):
    model = chat_db.session.scalars(select(AIModel).where(AIModel.name == "gpt-4o-mini")).one()
    endpoint = ModelEndpoint(ai_model_id=model.id, model_type="chat", adapter="litellm",
                             endpoint_url="https://custom.test/v1/chat/completions", api_base_url="https://custom.test/v1")
    chat_db.add(endpoint)
    await chat_db.commit()
    call = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                                                   model="actual", usage=None))
    monkeypatch.setattr(model_router.litellm, "acompletion", call)
    await model_router.call_llm("test", "general", db=chat_db)
    assert call.await_args.kwargs["api_base"] == "https://custom.test/v1"
    assert call.await_args.kwargs["api_key"] == "fake-secret"
    endpoint.model_type = "tts"
    endpoint.adapter = "http"
    await chat_db.commit()
    assert model.id not in [m.id for m in await model_router._pick_models(chat_db, "openai/gpt-4o-mini")]


async def test_media_endpoint_uses_full_url_db_keys_and_preserves_legacy_id(unit_db, monkeypatch):
    model = AIModel(name="shared-image", provider="openai")
    legacy = MediaModel(name="shared-image", provider="openai", media_type="image", is_async_poll=False)
    unit_db.add(model)
    unit_db.add(legacy)
    await unit_db.flush()
    endpoint = ModelEndpoint(ai_model_id=model.id, model_type="image", adapter="http", legacy_media_model_id=legacy.id,
                             endpoint_url="https://images.test/generate", api_base_url="https://images.test")
    endpoint.config = {"is_async_poll": False, "model_version": "image-model",
                       "response_extraction_config": {"type": "json_path", "path": "data[*].url"}}
    unit_db.add(endpoint)
    unit_db.add(ProviderAPIKey(provider="openai", label="media", encrypted_key="fake-secret"))
    await unit_db.commit()
    monkeypatch.setattr("app.services.key_manager.decrypt_token", lambda value: value)
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://files.test/image.png"}]})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(media_gen_service.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(provider), **kwargs))
    effective = await media_gen_service.get_media_model("shared-image", unit_db)
    assert effective.id == legacy.id and effective.name == model.name
    urls, kind = await media_gen_service.generate_media(unit_db, "shared-image", "test")
    assert urls == ["https://files.test/image.png"] and kind == "image"
    assert str(calls[0].url) == "https://images.test/generate"
    assert calls[0].headers["authorization"] == "Bearer fake-secret"
    unit_db.session.expire_all()
    assert unit_db.session.scalars(select(ProviderAPIKey)).one().requests_today == 1


async def test_tts_new_snapshot_uses_endpoint_without_rewriting_old_snapshot(unit_db, monkeypatch):
    model = AIModel(name="snapshot-tts", provider="gemini")
    legacy = TTSModel(name="snapshot-tts", provider="gemini", api_endpoint_url="https://old.test/tts")
    unit_db.add(model)
    unit_db.add(legacy)
    await unit_db.flush()
    endpoint = ModelEndpoint(ai_model_id=model.id, model_type="tts", adapter="http", legacy_tts_model_id=legacy.id,
                             endpoint_url="https://new.test/tts")
    unit_db.add(endpoint)
    await unit_db.commit()
    monkeypatch.setattr(generator, "AsyncSessionLocal", lambda: unit_db)
    new = await generator._load_model("snapshot-tts", None)
    assert new.api_endpoint_url == "https://new.test/tts" and new.model_endpoint_id == endpoint.id
    old = generator._ModelInfo(name="snapshot-tts", provider="gemini", is_local=False,
                               api_endpoint_url="https://old.test/tts", provider_config={},
                               default_voice="Kore", default_speed=1, default_lang="en")
    assert old.api_endpoint_url == "https://old.test/tts" and old.model_endpoint_id is None
