"""app/api/v1/keys/router.py — Management REST endpoints for AccessAPIKey."""

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
