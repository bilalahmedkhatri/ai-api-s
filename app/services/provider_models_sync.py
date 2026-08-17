"""app/services/provider_models_sync.py — Service module to fetch and sync Groq, Cohere, and NVIDIA models."""

import json
import logging
import urllib.request
import os
from sqlalchemy import select
from app.core.config import settings
from app.db.database import AsyncSessionLocal, init_db
from app.models.db_models import AIModel

logger = logging.getLogger(__name__)

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
COHERE_MODELS_URL = "https://api.cohere.com/v1/models"
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"


def fetch_groq_models() -> list[dict]:
    """Fetch model catalog from Groq API."""
    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY")
    headers = {"User-Agent": "AI-Gateway-ModelSync/1.0"}
    if groq_key:
        headers["Authorization"] = f"Bearer {groq_key}"

    models = []
    try:
        req = urllib.request.Request(GROQ_MODELS_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("data", []):
                    m_id = m.get("id", "").strip()
                    if m_id:
                        name = m_id if m_id.startswith("groq/") else f"groq/{m_id}"
                        models.append({
                            "name": name,
                            "provider": "groq",
                            "is_free": True,
                            "prompt_price_per_1k": 0.0,
                            "completion_price_per_1k": 0.0,
                        })
                logger.info("Fetched %d Groq models from API", len(models))
    except Exception as exc:
        logger.warning("Groq API fetch failed (%s), using fallback catalog", exc)

    if not models:
        fallback = [
            "groq/llama-3.3-70b-versatile",
            "groq/qwen-2.5-coder-32b",
            "groq/llama-3.1-8b-instant",
            "groq/mixtral-8x7b-32768",
            "groq/deepseek-r1-distill-llama-70b",
        ]
        for name in fallback:
            models.append({
                "name": name,
                "provider": "groq",
                "is_free": True,
                "prompt_price_per_1k": 0.0,
                "completion_price_per_1k": 0.0,
            })
    return models


def fetch_cohere_models() -> list[dict]:
    """Fetch model catalog from Cohere API."""
    cohere_key = settings.cohere_api_key or os.environ.get("COHERE_API_KEY")
    headers = {"User-Agent": "AI-Gateway-ModelSync/1.0"}
    if cohere_key:
        headers["Authorization"] = f"Bearer {cohere_key}"

    models = []
    try:
        req = urllib.request.Request(COHERE_MODELS_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("models", []):
                    m_id = m.get("name", "").strip()
                    if m_id:
                        name = m_id if m_id.startswith("cohere/") else f"cohere/{m_id}"
                        models.append({
                            "name": name,
                            "provider": "cohere",
                            "is_free": False,
                            "prompt_price_per_1k": 0.003,
                            "completion_price_per_1k": 0.015,
                        })
                logger.info("Fetched %d Cohere models from API", len(models))
    except Exception as exc:
        logger.warning("Cohere API fetch failed (%s), using fallback catalog", exc)

    if not models:
        fallback = [
            "cohere/command-r-plus",
            "cohere/command-r",
            "cohere/command-light",
        ]
        for name in fallback:
            models.append({
                "name": name,
                "provider": "cohere",
                "is_free": False,
                "prompt_price_per_1k": 0.003,
                "completion_price_per_1k": 0.015,
            })
    return models


def fetch_nvidia_models() -> list[dict]:
    """Fetch model catalog from NVIDIA API."""
    headers = {"User-Agent": "AI-Gateway-ModelSync/1.0"}
    models = []
    try:
        req = urllib.request.Request(NVIDIA_MODELS_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("data", []):
                    m_id = m.get("id", "").strip()
                    if m_id:
                        name = m_id if m_id.startswith("nvidia/") else f"nvidia/{m_id}"
                        models.append({
                            "name": name,
                            "provider": "nvidia",
                            "is_free": True,
                            "prompt_price_per_1k": 0.0,
                            "completion_price_per_1k": 0.0,
                        })
                logger.info("Fetched %d NVIDIA models from API", len(models))
    except Exception as exc:
        logger.warning("NVIDIA API fetch failed (%s), using fallback catalog", exc)

    if not models:
        fallback = [
            "nvidia/nemotron-4-340b-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "nvidia/mistral-neMo-12b-instruct",
        ]
        for name in fallback:
            models.append({
                "name": name,
                "provider": "nvidia",
                "is_free": True,
                "prompt_price_per_1k": 0.0,
                "completion_price_per_1k": 0.0,
            })
    return models


async def run_provider_models_sync(dry_run: bool = False) -> dict:
    """Sync models for Groq, Cohere, and NVIDIA to the database."""
    groq_models = fetch_groq_models()
    cohere_models = fetch_cohere_models()
    nvidia_models = fetch_nvidia_models()

    all_models = groq_models + cohere_models + nvidia_models

    await init_db()

    models_by_provider = {
        "groq": len(groq_models),
        "cohere": len(cohere_models),
        "nvidia": len(nvidia_models),
    }

    if dry_run:
        logger.info("[DRY RUN] Would sync %d provider models to database", len(all_models))
        return {
            "status": "dry_run",
            "providers_synced": ["groq", "cohere", "nvidia"],
            "models_by_provider": models_by_provider,
            "total_fetched": len(all_models),
            "total_synced": len(all_models),
        }

    inserted_count = 0
    updated_count = 0

    async with AsyncSessionLocal() as db:
        for m in all_models:
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

    logger.info("Provider models database sync complete. Inserted: %d, Updated: %d, Total active: %d",
                inserted_count, updated_count, len(all_models))

    return {
        "status": "success",
        "providers_synced": ["groq", "cohere", "nvidia"],
        "models_by_provider": models_by_provider,
        "inserted": inserted_count,
        "updated": updated_count,
        "total_active": len(all_models),
    }
