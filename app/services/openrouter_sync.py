"""app/services/openrouter_sync.py — Service module to fetch and sync OpenRouter free & low-cost models."""

import json
import logging
import urllib.request

from sqlalchemy import select
from app.db.database import AsyncSessionLocal, init_db
from app.models.db_models import AIModel

logger = logging.getLogger(__name__)
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def fetch_openrouter_models() -> list[dict]:
    """Fetch raw model catalog from OpenRouter public API."""
    logger.info("Fetching model catalog from OpenRouter (%s)...", OPENROUTER_MODELS_URL)
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"User-Agent": "AI-Gateway-ModelSync/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"OpenRouter API returned HTTP {resp.status}")
        data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        logger.info("Fetched %d total models from OpenRouter", len(models))
        return models


def parse_and_sort_models(raw_models: list[dict], max_paid: int = 50) -> tuple[list[dict], list[dict]]:
    """
    Parse raw models into free and cheap paid lists.
    Returns (free_models, cheap_paid_models).
    """
    free_models = []
    paid_models = []

    for m in raw_models:
        model_id = m.get("id", "").strip()
        if not model_id:
            continue

        pricing = m.get("pricing", {}) or {}
        try:
            prompt_token_usd = float(pricing.get("prompt", 0) or 0)
            completion_token_usd = float(pricing.get("completion", 0) or 0)
        except (ValueError, TypeError):
            prompt_token_usd = completion_token_usd = 0.0

        prompt_per_1k = round(prompt_token_usd * 1000, 6)
        completion_per_1k = round(completion_token_usd * 1000, 6)
        is_free = (prompt_per_1k == 0.0 and completion_per_1k == 0.0)

        provider = "openrouter"
        if "/" in model_id:
            provider = model_id.split("/")[0]

        name = f"openrouter/{model_id}" if not model_id.startswith("openrouter/") else model_id

        entry = {
            "name": name,
            "provider": provider,
            "is_free": is_free,
            "prompt_price_per_1k": prompt_per_1k,
            "completion_price_per_1k": completion_per_1k,
            "total_1k_cost": prompt_per_1k + completion_per_1k,
        }

        if is_free:
            free_models.append(entry)
        else:
            paid_models.append(entry)

    paid_models.sort(key=lambda x: x["total_1k_cost"])
    selected_paid = paid_models[:max_paid]

    logger.info("Categorized models: %d FREE, %d SELECTED CHEAP PAID (from %d total paid)",
                len(free_models), len(selected_paid), len(paid_models))

    return free_models, selected_paid


async def run_openrouter_model_sync(max_paid: int = 50, dry_run: bool = False) -> dict:
    """Core entrypoint to sync OpenRouter models to AI Gateway database."""
    raw_models = fetch_openrouter_models()
    free_models, cheap_paid_models = parse_and_sort_models(raw_models, max_paid=max_paid)

    all_targets = free_models + cheap_paid_models

    await init_db()

    if dry_run:
        logger.info("[DRY RUN] Would sync %d models to database", len(all_targets))
        return {
            "status": "dry_run",
            "total_fetched": len(raw_models),
            "free_models_count": len(free_models),
            "paid_models_selected": len(cheap_paid_models),
            "total_synced": len(all_targets),
        }

    inserted_count = 0
    updated_count = 0

    async with AsyncSessionLocal() as db:
        for m in all_targets:
            stmt = select(AIModel).where(AIModel.name == m["name"])
            res = await db.execute(stmt)
            existing = res.scalar_one_or_none()

            if existing:
                existing.provider = m["provider"]
                existing.is_free = m["is_free"]
                existing.prompt_price_per_1k = m["prompt_price_per_1k"]
                existing.completion_price_per_1k = m["completion_price_per_1k"]
                existing.is_active = True
                updated_count += 1
            else:
                new_model = AIModel(
                    name=m["name"],
                    provider=m["provider"],
                    is_active=True,
                    is_free=m["is_free"],
                    prompt_price_per_1k=m["prompt_price_per_1k"],
                    completion_price_per_1k=m["completion_price_per_1k"],
                )
                db.add(new_model)
                inserted_count += 1

        await db.commit()

    logger.info("Database sync complete. Inserted: %d, Updated: %d, Total active: %d",
                inserted_count, updated_count, len(all_targets))

    return {
        "status": "success",
        "total_fetched": len(raw_models),
        "free_models_count": len(free_models),
        "paid_models_selected": len(cheap_paid_models),
        "inserted": inserted_count,
        "updated": updated_count,
        "total_active": len(all_targets),
    }
