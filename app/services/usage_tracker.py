"""Service for tracking and aggregating AI model token usage per website/origin."""

import datetime
import logging
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AIModel, ModelWebsiteUsage

logger = logging.getLogger(__name__)


async def get_or_create_model(
    db: AsyncSession,
    model_name: str,
    provider: str = "groq",
    is_free: bool = True,
    prompt_price_per_1k: float = 0.0,
    completion_price_per_1k: float = 0.0,
) -> AIModel:
    """Retrieves an existing AIModel or registers a new one."""
    stmt = select(AIModel).where(AIModel.name == model_name)
    res = await db.execute(stmt)
    model = res.scalar_one_or_none()

    if not model:
        model = AIModel(
            name=model_name,
            provider=provider,
            is_active=True,
            is_free=is_free,
            prompt_price_per_1k=prompt_price_per_1k,
            completion_price_per_1k=completion_price_per_1k,
        )
        db.add(model)
        await db.flush()
        logger.info("Registered new AIModel: %s (provider=%s, free=%s)", model_name, provider, is_free)

    return model


async def record_model_usage(
    db: AsyncSession,
    model_name: str,
    website_origin: str,
    prompt_tokens: int,
    completion_tokens: int,
    provider: str = "groq",
    is_free: bool = True,
) -> ModelWebsiteUsage:
    """
    Accumulates daily token usage and estimated cost for a given model and website origin.
    Uses PostgreSQL atomic ON CONFLICT DO UPDATE (upsert) for maximum performance.
    """
    model = await get_or_create_model(db, model_name=model_name, provider=provider, is_free=is_free)

    p_tokens = max(0, prompt_tokens or 0)
    c_tokens = max(0, completion_tokens or 0)
    tot_tokens = p_tokens + c_tokens

    # Calculate cost if model is paid
    cost = 0.0
    if not model.is_free:
        cost = (p_tokens / 1000.0 * model.prompt_price_per_1k) + (c_tokens / 1000.0 * model.completion_price_per_1k)

    today = datetime.date.today()
    clean_origin = (website_origin or "unknown").strip().lower()

    # Clean ORM pattern with row locking for safe atomic updates
    stmt = (
        select(ModelWebsiteUsage)
        .where(
            ModelWebsiteUsage.model_id == model.id,
            ModelWebsiteUsage.website_origin == clean_origin,
            ModelWebsiteUsage.usage_date == today,
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    usage_row = res.scalar_one_or_none()

    if usage_row:
        usage_row.total_requests += 1
        usage_row.prompt_tokens += p_tokens
        usage_row.completion_tokens += c_tokens
        usage_row.total_tokens += tot_tokens
        usage_row.total_cost_usd += cost
        usage_row.last_updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        usage_row = ModelWebsiteUsage(
            model_id=model.id,
            website_origin=clean_origin,
            usage_date=today,
            total_requests=1,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=tot_tokens,
            total_cost_usd=cost,
        )
        db.add(usage_row)

    await db.commit()
    await db.refresh(usage_row)

    return usage_row
