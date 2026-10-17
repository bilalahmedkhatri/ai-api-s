"""Catalog registration uses isolated SQL, without provider calls."""
# ruff: noqa: F811
from unittest.mock import AsyncMock

import pytest
from app.models.dashboard import AuditLog
from app.models.db_models import AIModel, ProviderAPIKey
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

from tests.test_dashboard import dashboard  # noqa: F401

URL = "/api/v1/dashboard/provider-models"


@pytest.mark.asyncio
async def test_register_all_modalities_duplicate_and_safe_listing(dashboard):
    db, member, headers = dashboard
    db.rollback = AsyncMock(side_effect=db.session.rollback)
    db.add(ProviderAPIKey(provider="openai", label="isolated", encrypted_key="never-list-this"))
    db.add(ProviderAPIKey(provider="openai", label="second", encrypted_key="also-private"))
    db.add(ProviderAPIKey(provider="groq", label="disabled", encrypted_key="private", is_active=False))
    db.add(AIModel(name="catalog-only", provider="not-a-saved-provider"))
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for name in ["chat", "tts", "voice", "image", "video", "embeddings"]:
            result = await client.post(URL, headers=headers, json={"name": name, "provider": "OpenAI"})
            assert result.status_code == 201
        duplicate = await client.post(URL, headers=headers, json={"name": "chat", "provider": "openai"})
        assert duplicate.status_code == 409
        result = await client.get(URL, headers=headers)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert len(result.json()["models"]) == 7
        assert result.json()["providers"] == ["groq", "openai"]
        assert "never-list-this" not in result.text and "encrypted_key" not in result.text
        assert len((await db.execute(select(AuditLog).where(AuditLog.action == "CREATE_PROVIDER_MODEL"))).scalars().all()) == 6


@pytest.mark.asyncio
async def test_admin_origin_and_validation(dashboard):
    db, member, headers = dashboard
    db.add(ProviderAPIKey(provider="groq", label="test", encrypted_key="private"))
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = {"name": "paid", "provider": "groq", "is_free": False,
                "prompt_price_per_1k": 0.002, "completion_price_per_1k": 0.004}
        assert (await client.get(URL)).status_code == 401
        assert (await client.post(URL, headers={**headers, "Origin": "https://foreign.test"}, json=body)).status_code == 403
        for invalid in [{"name": " "}, {"provider": "bad/provider"}, {"prompt_price_per_1k": -1}, {"api_key": "forbidden"}]:
            assert (await client.post(URL, headers=headers, json={**body, **invalid})).status_code == 422
        assert (await client.post(URL, headers=headers, json={**body, "provider": "unregistered"})).status_code == 400
        assert (await client.post(URL, headers=headers, json=body)).status_code == 201
        model = (await db.execute(select(AIModel).where(AIModel.name == "paid"))).scalar_one()
        assert model.prompt_price_per_1k == 0.002 and model.completion_price_per_1k == 0.004
        member.role = "viewer"
        await db.commit()
        assert (await client.get(URL, headers=headers)).status_code == 403
        assert (await client.post(URL, headers=headers, json=body)).status_code == 403
