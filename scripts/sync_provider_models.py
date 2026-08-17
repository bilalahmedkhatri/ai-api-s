"""
scripts/sync_provider_models.py — CLI tool to fetch and sync Groq, Cohere, and NVIDIA models.

Usage:
  python scripts/sync_provider_models.py
  python scripts/sync_provider_models.py --dry-run
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root directory to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.provider_models_sync import run_provider_models_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Sync Groq, Cohere, and NVIDIA models to AI Gateway database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview model classification without database writes.",
    )
    args = parser.parse_args()

    logger.info("Starting Provider Model Sync (Groq, Cohere, NVIDIA)...")
    res = asyncio.run(run_provider_models_sync(dry_run=args.dry_run))
    print(f"\nSync Result: {res}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
