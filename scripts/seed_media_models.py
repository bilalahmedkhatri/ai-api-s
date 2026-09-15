"""scripts/seed_media_models.py — Pre-populates the database with dynamic image/video models."""

import asyncio
import os
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db, init_db
from app.models.db_models import MediaModel


async def seed():
    await init_db()
    async for db in get_db():
        await run_seed(db)
        break


async def run_seed(db: AsyncSession):
    models = [
        # --- IMAGE MODELS ---
        MediaModel(
            name="flux-schnell",
            display_name="Flux Schnell (Replicate)",
            media_type="image",
            provider="replicate",
            is_active=bool(os.environ.get("REPLICATE_API_TOKEN")),
            api_base_url="https://api.replicate.com/v1",
            api_endpoint_path="/models/black-forest-labs/flux-schnell/predictions",
            model_version="black-forest-labs/flux-schnell",
            auth_env_var="REPLICATE_API_TOKEN",
            is_async_poll=True,
            poll_endpoint_path="/predictions/{id}",
            poll_status_field="status",
            poll_success_value="succeeded",
            poll_failed_value="failed",
            poll_timeout_s=120,
            poll_interval_s=2,
            response_extraction_config={"type": "json_path", "path": "output[*]", "item_type": "url"},
            returns_base64=False,
            default_width=1024,
            default_height=1024,
            max_outputs=4,
            default_params={"output_format": "webp"}
        ),
        MediaModel(
            name="dall-e-3",
            display_name="DALL-E 3 (OpenAI)",
            media_type="image",
            provider="openai",
            is_active=bool(os.environ.get("OPENAI_API_KEY")),
            api_base_url="https://api.openai.com/v1",
            api_endpoint_path="/images/generations",
            model_version="dall-e-3",
            auth_env_var="OPENAI_API_KEY",
            is_async_poll=False,
            response_extraction_config={"type": "json_path", "path": "data[*].url", "item_type": "url"},
            returns_base64=False,
            default_width=1024,
            default_height=1024,
            max_outputs=1,
            default_params={}
        ),
        MediaModel(
            name="stable-diffusion-xl",
            display_name="SDXL Core (Stability AI)",
            media_type="image",
            provider="stability",
            is_active=bool(os.environ.get("STABILITY_API_KEY")),
            api_base_url="https://api.stability.ai",
            api_endpoint_path="/v2beta/stable-image/generate/core",
            model_version="core",
            auth_env_var="STABILITY_API_KEY",
            is_async_poll=False,
            response_extraction_config={"type": "binary", "mime": "image/png"},
            returns_base64=True,
            default_width=1024,
            default_height=1024,
            max_outputs=1,
            default_params={}
        ),
        # --- VIDEO MODELS ---
        MediaModel(
            name="luma-dream-machine",
            display_name="Luma Dream Machine (FAL)",
            media_type="video",
            provider="fal",
            is_active=bool(os.environ.get("FAL_KEY")),
            api_base_url="https://queue.fal.run",
            api_endpoint_path="/fal-ai/luma-dream-machine",
            model_version="fal-ai/luma-dream-machine",
            auth_env_var="FAL_KEY",
            is_async_poll=True,
            poll_endpoint_path="/fal-ai/luma-dream-machine/requests/{id}",
            poll_status_field="status",
            poll_success_value="COMPLETED",
            poll_failed_value="FAILED",
            poll_timeout_s=600,
            poll_interval_s=5,
            response_extraction_config={"type": "json_path", "path": "video.url", "item_type": "url"},
            returns_base64=False,
            default_width=1280,
            default_height=720,
            max_outputs=1,
            default_params={}
        ),
        MediaModel(
            name="kling-v1",
            display_name="Kling V1 (Replicate)",
            media_type="video",
            provider="replicate",
            is_active=bool(os.environ.get("REPLICATE_API_TOKEN")),
            api_base_url="https://api.replicate.com/v1",
            api_endpoint_path="/models/klingai/kling-v1/predictions",
            model_version="klingai/kling-v1",
            auth_env_var="REPLICATE_API_TOKEN",
            is_async_poll=True,
            poll_endpoint_path="/predictions/{id}",
            poll_status_field="status",
            poll_success_value="succeeded",
            poll_failed_value="failed",
            poll_timeout_s=600,
            poll_interval_s=5,
            response_extraction_config={"type": "json_path", "path": "output[*]", "item_type": "url"},
            returns_base64=False,
            default_width=1280,
            default_height=720,
            max_outputs=1,
            default_params={}
        )
    ]

    for m in models:
        # check if exists
        from sqlalchemy import select
        existing = await db.execute(select(MediaModel).where(MediaModel.name == m.name))
        if not existing.scalar_one_or_none():
            db.add(m)
            print(f"Added {m.name} (active={m.is_active})")
        else:
            print(f"Skipped {m.name} (already exists)")
            
    await db.commit()
    print("Seeding complete.")

if __name__ == "__main__":
    asyncio.run(seed())
