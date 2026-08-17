"""Unit tests for AI Models registry and aggregated token usage tracking."""

import datetime
import pytest
from sqlalchemy import select

from app.db.database import AsyncSessionLocal, init_db
from app.models.db_models import AIModel, ModelWebsiteUsage
from app.services.usage_tracker import get_or_create_model, record_model_usage


@pytest.mark.asyncio
async def test_ai_model_registration():
    await init_db()
    async with AsyncSessionLocal() as session:
        # Register a free model
        m1 = await get_or_create_model(session, model_name="groq/qwen-2.5-coder-32b", provider="groq", is_free=True)
        assert m1.id is not None
        assert m1.name == "groq/qwen-2.5-coder-32b"
        assert m1.is_free is True

        # Register a paid model with custom pricing per 1k tokens
        m2 = await get_or_create_model(
            session,
            model_name="openai/gpt-4o-paid",
            provider="openai",
            is_free=False,
            prompt_price_per_1k=0.0025,
            completion_price_per_1k=0.010,
        )
        assert m2.id is not None
        assert m2.is_free is False
        assert m2.prompt_price_per_1k == 0.0025


@pytest.mark.asyncio
async def test_record_model_usage_aggregation():
    await init_db()
    async with AsyncSessionLocal() as session:
        model_name = "test-model-aggregator"
        origin = "dashboard.mycompany.com"

        # First request: 100 prompt, 50 completion tokens
        row1 = await record_model_usage(
            session,
            model_name=model_name,
            website_origin=origin,
            prompt_tokens=100,
            completion_tokens=50,
            provider="test-provider",
            is_free=True,
        )
        assert row1.total_requests >= 1
        assert row1.website_origin == origin

        # Second request on same date: 200 prompt, 100 completion tokens
        row2 = await record_model_usage(
            session,
            model_name=model_name,
            website_origin=origin,
            prompt_tokens=200,
            completion_tokens=100,
            provider="test-provider",
            is_free=True,
        )

        # Check that tokens accumulated under same record
        assert row2.id == row1.id
        assert row2.prompt_tokens >= 300
        assert row2.completion_tokens >= 150
        assert row2.total_tokens >= 450
