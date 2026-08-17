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
import sys
from pathlib import Path

# Ensure project root is in sys.path when invoked directly or via cron
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.openrouter_sync import run_openrouter_model_sync


async def main():
    parser = argparse.ArgumentParser(description="Sync OpenRouter free and cheap models to AI Gateway database")
    parser.add_argument("--dry-run", action="store_true", help="Preview models to sync without altering database")
    parser.add_argument("--max-paid", type=int, default=50, help="Maximum number of lowest-cost paid models to include")
    args = parser.parse_args()

    result = await run_openrouter_model_sync(max_paid=args.max_paid, dry_run=args.dry_run)
    print("Sync Result:", result)


if __name__ == "__main__":
    asyncio.run(main())
