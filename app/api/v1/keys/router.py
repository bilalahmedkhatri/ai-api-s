"""app/api/v1/keys/router.py — Management REST endpoints for AccessAPIKey and ProviderAPIKey."""

import logging
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.cron import verify_cron_authorization
from app.db.database import get_db
from app.services.api_key_service import (
    generate_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.services.key_manager import (
    add_provider_key,
    list_provider_keys,
    update_provider_key,
    toggle_key,
    delete_provider_key,
    reset_daily_counts,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/keys", tags=["Access API Keys Management"])


class CreateKeyRequest(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=128, description="Third-party app/website name")
    allowed_domain: str | None = Field(default=None, description="Domain constraint e.g. client.com (optional)")


class CreateKeyResponse(BaseModel):
    id: int
    raw_key: str = Field(..., description="Raw secret API Key. Store securely — displayed ONLY ONCE!")
    key_prefix: str
    client_name: str
    allowed_domain: str | None
    is_active: bool
    created_at: datetime


class KeyListItem(BaseModel):
    id: int
    key_prefix: str
    client_name: str
    allowed_domain: str | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


@router.post("/", response_model=CreateKeyResponse, status_code=status.HTTP_201_CREATED, summary="Create a new AccessAPIKey")
async def create_key_endpoint(
    req: CreateKeyRequest,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """
    Admin endpoint to generate a new AccessAPIKey for a third-party website/app.
    Requires CRON_SECRET / Admin Authorization header if configured.
    """
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    raw_key, record = await generate_api_key(
        db, client_name=req.client_name, allowed_domain=req.allowed_domain
    )
    return CreateKeyResponse(
        id=record.id,
        raw_key=raw_key,
        key_prefix=record.key_prefix,
        client_name=record.client_name,
        allowed_domain=record.allowed_domain,
        is_active=record.is_active,
        created_at=record.created_at,
    )


@router.get("/", response_model=list[KeyListItem], summary="List all AccessAPIKeys")
async def list_keys_endpoint(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """Admin endpoint to list registered API keys."""
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    records = await list_api_keys(db)
    return [
        KeyListItem(
            id=r.id,
            key_prefix=r.key_prefix,
            client_name=r.client_name,
            allowed_domain=r.allowed_domain,
            is_active=r.is_active,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
        )
        for r in records
    ]


@router.delete("/{key_id}", summary="Revoke/deactivate an AccessAPIKey")
async def revoke_key_endpoint(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """Admin endpoint to revoke/deactivate an AccessAPIKey."""
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    success = await revoke_api_key(db, key_id)
    if not success:
        raise HTTPException(status_code=404, detail="AccessAPIKey not found")
    return {"status": "success", "message": f"AccessAPIKey id={key_id} revoked"}


# ── Provider API Keys ────────────────────────────────────────────────────────

class AddProviderKeyRequest(BaseModel):
    provider: str = Field(..., description="Provider name e.g. gemini, openai, groq, brave")
    plain_key: str = Field(..., min_length=10, description="The raw API key — will be encrypted before storage")
    label: str = Field(..., min_length=1, max_length=128, description="Friendly label e.g. account-1, personal")
    priority: int = Field(default=1, ge=1, description="Rotation priority — lower = used first")
    daily_limit: int | None = Field(default=None, ge=1, description="Max requests per day (None = unlimited)")


@router.post(
    "/provider",
    status_code=status.HTTP_201_CREATED,
    summary="Add an encrypted provider API key (Gemini, OpenAI, Groq…)",
    tags=["Provider API Keys"],
)
async def add_provider_key_endpoint(
    req: AddProviderKeyRequest,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """
    Admin endpoint to add a new provider API key.
    The key is Fernet-encrypted before being stored — plain text is never saved.
    """
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    try:
        key_obj = await add_provider_key(
            db,
            provider=req.provider,
            plain_key=req.plain_key,
            label=req.label,
            priority=req.priority,
            daily_limit=req.daily_limit,
        )
        return {
            "status": "created",
            "id": key_obj.id,
            "provider": key_obj.provider,
            "label": key_obj.label,
            "priority": key_obj.priority,
            "daily_limit": key_obj.daily_limit,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/provider/{provider}",
    summary="List all keys for a provider (metadata only — no decrypted keys)",
    tags=["Provider API Keys"],
)
async def list_provider_keys_endpoint(
    provider: str,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    keys = await list_provider_keys(db, provider=provider)
    return {"provider": provider, "count": len(keys), "keys": keys}


@router.patch(
    "/provider/{key_id}/toggle",
    summary="Enable or disable a provider API key",
    tags=["Provider API Keys"],
)
async def toggle_provider_key_endpoint(
    key_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    success = await toggle_key(db, key_id=key_id, is_active=is_active)
    if not success:
        raise HTTPException(status_code=404, detail="Provider key not found")
    return {"status": "updated", "key_id": key_id, "is_active": is_active}


class UpdateProviderKeyRequest(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64, description="New provider identifier (e.g. gemini, openai, groq, brave)")
    label: str | None = Field(default=None, min_length=1, max_length=128, description="New friendly label")
    plain_key: str | None = Field(default=None, min_length=10, description="New raw API key — will be re-encrypted")
    priority: int | None = Field(default=None, ge=1, description="New rotation priority")
    daily_limit: int | None = Field(default=None, ge=1, description="New daily request limit (None = unlimited)")


@router.patch(
    "/provider/{key_id}",
    summary="Update an existing provider API key (provider, label, key value, priority, daily limit)",
    tags=["Provider API Keys"],
)
async def update_provider_key_endpoint(
    key_id: int,
    req: UpdateProviderKeyRequest,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """
    Partially update a provider API key.
    Only the fields you provide will be changed — omitted fields stay as-is.
    If plain_key is provided it will be re-encrypted before storing.
    """
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    try:
        updated = await update_provider_key(
            db,
            key_id=key_id,
            provider=req.provider,
            label=req.label,
            plain_key=req.plain_key,
            priority=req.priority,
            daily_limit=req.daily_limit,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Provider key id={key_id} not found")
        return {"status": "updated", **updated}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/provider/{key_id}",
    summary="Permanently delete a provider API key",
    tags=["Provider API Keys"],
)
async def delete_provider_key_endpoint(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    success = await delete_provider_key(db, key_id=key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Provider key not found")
    return {"status": "deleted", "key_id": key_id}


@router.post(
    "/provider/reset-daily",
    summary="Reset daily request counters (run at midnight UTC)",
    tags=["Provider API Keys"],
)
async def reset_daily_counts_endpoint(
    provider: str | None = None,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    cron_secret_header: str | None = Header(default=None, alias="Cron-Secret"),
    cron_secret_env_header: str | None = Header(default=None, alias="CRON_SECRET"),
):
    """Resets requests_today=0 and re-enables quota-exhausted keys. Call once per day."""
    verify_cron_authorization(
        authorization=authorization,
        x_cron_secret=x_cron_secret,
        cron_secret_header=cron_secret_header,
        cron_secret_env_header=cron_secret_env_header,
    )
    count = await reset_daily_counts(db, provider=provider)
    return {"status": "reset", "keys_affected": count, "provider": provider or "all"}
