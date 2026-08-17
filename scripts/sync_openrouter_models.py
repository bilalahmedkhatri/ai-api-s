#!/usr/bin/env python3
"""scripts/sync_openrouter_models.py — Sync free and lowest-cost models from OpenRouter to AI Gateway DB.

Fetches model catalog from OpenRouter API (https://openrouter.ai/api/v1/models).
Filters and prioritises:
  1. All FREE models (prompt & completion pricing == 0)
  2. Cheapest paid models (sorted by lowest combined 1k-token cost)

Upserts model metadata into the `ai_models` database table.

Usage:
  # Dry run (preview without DB changes)
  python scripts/sync_openrouter_models.py --dry-run

  # Daily cron run (syncs all free models + top 50 cheapest paid models)
  0 3 * * * /app/.venv/bin/python /app/scripts/sync_openrouter_models.py
"""

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
from pathlib import Path

# Ensure project root is in sys.path when invoked directly or via cron
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select
from app.db.database import AsyncSessionLocal, init_db
from app.models.db_models import AIModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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
    Parse models into free and cheap paid lists.
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

        # Convert price per token (USD) to price per 1,000 tokens (USD)
        prompt_per_1k = round(prompt_token_usd * 1000, 6)
        completion_per_1k = round(completion_token_usd * 1000, 6)
        is_free = (prompt_per_1k == 0.0 and completion_per_1k == 0.0)

        # Extract provider name
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

    # Sort paid models by lowest combined cost per 1k tokens
    paid_models.sort(key=lambda x: x["total_1k_cost"])
    selected_paid = paid_models[:max_paid]

    logger.info("Categorized models: %d FREE, %d SELECTED CHEAP PAID (from %d total paid)",
                len(free_models), len(selected_paid), len(paid_models))

    return free_models, selected_paid


async def sync_models_to_db(models_to_sync: list[dict], dry_run: bool = False) -> None:
    """Upsert list of parsed models into the `ai_models` database table."""
    await init_db()

    if dry_run:
        logger.info("[DRY RUN] Would sync %d models to database", len(models_to_sync))
        for m in models_to_sync[:10]:
            status = "FREE" if m["is_free"] else f"${m['total_1k_cost']:.6f}/1k"
            logger.info("  [DRY RUN] %s | Provider: %s | Status: %s", m["name"], m["provider"], status)
        if len(models_to_sync) > 10:
            logger.info("  ... and %d more models.", len(models_to_sync) - 10)
        return

    inserted_count = 0
    updated_count = 0

    async with AsyncSessionLocal() as db:
        for m in models_to_sync:
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
                inserted_count, updated_count, len(models_to_sync))


async def main():
    parser = argparse.ArgumentParser(description="Sync OpenRouter free and cheap models to AI Gateway database")
    parser.add_argument("--dry-run", action="store_true", help="Preview models to sync without altering database")
    parser.add_argument("--max-paid", type=int, default=50, help="Maximum number of lowest-cost paid models to include")
    args = parser.parse_args()

    raw_models = fetch_openrouter_models()
    free_models, cheap_paid_models = parse_and_sort_models(raw_models, max_paid=args.max_paid)

    # Primary target: ALL free models FIRST, followed by cheapest paid models
    all_targets = free_models + cheap_paid_models

    await sync_models_to_db(all_targets, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
