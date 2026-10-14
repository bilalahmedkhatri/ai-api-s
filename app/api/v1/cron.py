"""app/api/v1/cron.py — HTTP Cron Trigger endpoints (Upstash QStash / external webhooks)."""

import logging
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.database import get_db
from app.services.openrouter_sync import run_openrouter_model_sync
from app.services.provider_models_sync import run_provider_models_sync
from app.services.key_manager import reset_daily_counts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["Cron Trigger"])


def verify_cron_authorization(
    authorization: str | None = None,
    upstash_authorization: str | None = None,
    x_cron_secret: str | None = None,
    upstash_x_cron_secret: str | None = None,
    cron_secret_header: str | None = None,
    cron_secret_env_header: str | None = None,
) -> None:
    """Validate incoming cron request against settings.cron_secret if configured."""
    if not settings.cron_secret:
        raise HTTPException(status_code=503, detail="Admin authorization is not configured")

    token = None
    auth_val = authorization or upstash_authorization
    if auth_val and auth_val.startswith("Bearer "):
        token = auth_val.split("Bearer ", 1)[1].strip()
    elif auth_val:
        token = auth_val.strip()
    elif x_cron_secret:
        token = x_cron_secret.strip()
    elif upstash_x_cron_secret:
        token = upstash_x_cron_secret.strip()
    elif cron_secret_header:
        token = cron_secret_header.strip()
    elif cron_secret_env_header:
        token = cron_secret_env_header.strip()

    if token != settings.cron_secret:
        logger.warning("Unauthorized cron trigger attempt from HTTP client")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing cron authorization secret header",
        )


@router.post("/sync-models", summary="Trigger OpenRouter model sync (Cron Endpoint)")
@router.get("/sync-models", summary="Trigger OpenRouter model sync via GET (Cron Endpoint)")
async def sync_models_endpoint(
    dry_run: bool = Query(default=False, description="Preview without writing to DB"),
    max_paid: int = Query(default=50, description="Max lowest-cost paid models to include"),
    authorization: str | None = Header(default=None),
    upstash_authorization: str | None = Header(default=None, alias="Upstash-Forward-Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    upstash_x_cron_secret: str | None = Header(default=None, alias="Upstash-Forward-X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """
    HTTP trigger for Upstash QStash / external cron services.
    Fetches OpenRouter free and low-cost models and updates the `ai_models` database table.
    """
    verify_cron_authorization(
        authorization=authorization,
        upstash_authorization=upstash_authorization,
        x_cron_secret=x_cron_secret,
        upstash_x_cron_secret=upstash_x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    try:
        result = await run_openrouter_model_sync(max_paid=max_paid, dry_run=dry_run)
        return result
    except Exception as exc:
        logger.error("Error executing model sync: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model sync failed: {str(exc)}",
        )


@router.post("/sync-provider-models", summary="Trigger Groq, Cohere & NVIDIA model sync (Cron Endpoint)")
@router.get("/sync-provider-models", summary="Trigger Groq, Cohere & NVIDIA model sync via GET (Cron Endpoint)")
@router.post("/sync-other-models", summary="Trigger Groq, Cohere & NVIDIA model sync (Alias Cron Endpoint)")
@router.get("/sync-other-models", summary="Trigger Groq, Cohere & NVIDIA model sync via GET (Alias Cron Endpoint)")
async def sync_provider_models_endpoint(
    dry_run: bool = Query(default=False, description="Preview without writing to DB"),
    authorization: str | None = Header(default=None),
    upstash_authorization: str | None = Header(default=None, alias="Upstash-Forward-Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    upstash_x_cron_secret: str | None = Header(default=None, alias="Upstash-Forward-X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """
    HTTP trigger for Upstash QStash / external cron services.
    Fetches Groq, Cohere, and NVIDIA models and updates the `ai_models` database table.
    """
    verify_cron_authorization(
        authorization=authorization,
        upstash_authorization=upstash_authorization,
        x_cron_secret=x_cron_secret,
        upstash_x_cron_secret=upstash_x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    try:
        result = await run_provider_models_sync(dry_run=dry_run)
        return result
    except Exception as exc:
        logger.error("Error executing provider model sync: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Provider model sync failed: {str(exc)}",
        )


@router.post("/reset-daily-keys", summary="Reset provider API key daily counters (run at midnight UTC)")
@router.get("/reset-daily-keys", summary="Reset provider API key daily counters via GET (Cron-compatible)")
async def reset_daily_keys_endpoint(
    provider: str | None = Query(default=None, description="Provider to reset (e.g. 'gemini'). Omit to reset ALL providers."),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    upstash_authorization: str | None = Header(default=None, alias="Upstash-Forward-Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    upstash_x_cron_secret: str | None = Header(default=None, alias="Upstash-Forward-X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """
    Resets `requests_today = 0` and re-enables all quota-exhausted provider keys.

    Schedule this endpoint to fire daily at **00:00 UTC** via Upstash QStash or any cron
    service so the DB-backed Gemini key rotation pool refreshes automatically every day.

    - Pass `?provider=gemini` to reset only Gemini keys.
    - Omit the param to reset ALL provider keys (gemini, openai, groq, etc.).
    """
    verify_cron_authorization(
        authorization=authorization,
        upstash_authorization=upstash_authorization,
        x_cron_secret=x_cron_secret,
        upstash_x_cron_secret=upstash_x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    try:
        count = await reset_daily_counts(db, provider=provider)
        logger.info("Cron: reset daily key counters — %d keys affected (provider=%s)", count, provider or "all")
        return {
            "status": "reset",
            "keys_affected": count,
            "provider": provider or "all",
        }
    except Exception as exc:
        logger.error("Error resetting daily key counts: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Daily key reset failed: {str(exc)}",
        )
