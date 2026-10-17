"""Unit tests for AI Models registry and aggregated token usage tracking."""

import datetime

import pytest
from app.db.database import AsyncSessionLocal, init_db
from app.models.db_models import AIModel, ModelWebsiteUsage
from app.services.usage_tracker import get_or_create_model, record_model_usage
from sqlalchemy import select


def test_concurrent_daily_accounting_uses_atomic_upsert(tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from app.models.db_models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from tests.test_app_authorization import AsyncAdapter

    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(AIModel(name="known-chat", provider="openai"))
            session.commit()

        def record(_):
            with Session(engine, expire_on_commit=False) as session:
                asyncio.run(record_model_usage(AsyncAdapter(session), "known-chat", "app:1", 2, 1))

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(record, range(24)))
        with Session(engine) as session:
            usage = session.scalars(select(ModelWebsiteUsage)).one()
            assert (usage.total_requests, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (24, 48, 24, 72)
    finally:
        engine.dispose()


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
