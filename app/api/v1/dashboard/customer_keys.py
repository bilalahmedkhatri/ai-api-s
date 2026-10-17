"""Session-authenticated customer API keys; admin-visible master keys stay separate."""
import hmac
import json

from app.api.v1.apps.router import available_endpoints
from app.core.middleware import limiter
from app.db.database import get_db
from app.models.dashboard import Membership
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.api_key_service import generate_api_key
from app.services.customer_keys import ensure_master_key
from app.services.dashboard import (
    audit,
    aware,
    check_origin,
    cipher,
    digest,
    member,
    now,
    require_admin,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class CreateCustomerKey(BaseModel):
    name: str = Field(min_length=2, max_length=128, pattern=r"\S")
    master_key: SecretStr | None = None


def metadata(key):
    return {
        "id": key.id, "name": key.client_name, "prefix": key.key_prefix,
        "is_active": key.is_active, "created_at": key.created_at,
        "last_used_at": key.last_used_at,
    }


@router.post("/members/{identifier}/api-access")
async def enable_api_access(identifier: int, request: Request, response: Response,
                            m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    target = await db.get(Membership, identifier)
    if not target or target.status != "approved":
        raise HTTPException(400, "Approve this membership before enabling API keys")
    ensure_master_key(target)
    await audit(db, m, "ENABLE_API_KEYS", str(target.id))
    await db.commit()
    return {"success": True}


@router.get("/api-keys")
async def list_customer_keys(response: Response, m=Depends(member), db=Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    keys = (await db.execute(select(AccessAPIKey).where(
        AccessAPIKey.authorized_app_id == m.api_project_id
    ).order_by(AccessAPIKey.created_at.desc()))).scalars().all() if m.api_project_id else []
    return {
        "enabled": m.role == "admin" or bool(m.master_key_encrypted),
        "unlocked": m.role == "admin" or m.api_keys_unlocked,
        "keys": [metadata(key) for key in keys],
    }


@router.post("/api-keys", status_code=201)
@limiter.limit("10/minute", key_func=lambda request: str(request.state.dashboard_membership.id))
async def create_customer_key(body: CreateCustomerKey, request: Request, response: Response,
                               m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    # Serialize project creation and unlocking on the membership, not process memory.
    locked = (await db.execute(select(Membership).where(Membership.id == m.id)
                              .with_for_update().execution_options(populate_existing=True))).scalar_one()
    is_admin = locked.role == "admin"
    if (locked.status != "approved" or (not is_admin and not locked.master_key_encrypted)
        or (locked.access_expires_at and aware(locked.access_expires_at) <= now())):
        raise HTTPException(403, "Ask an administrator to enable API keys")
    if not is_admin and not locked.api_keys_unlocked:
        expected = cipher().decrypt(locked.master_key_encrypted.encode()).decode()
        supplied = body.master_key.get_secret_value() if body.master_key else ""
        if len(supplied) > 256 or not hmac.compare_digest(digest(supplied), digest(expected)):
            await audit(db, m, "API_KEY_ACTIVATION_FAILED", str(m.id), 403)
            await db.commit()
            raise HTTPException(403, "Master key is incorrect")
    owner = await db.get(User, locked.user_id)
    if not owner or not owner.is_active:
        raise HTTPException(403, "An active verified gateway account is required")
    if locked.api_project_id:
        project = await db.get(AuthorizedApp, locked.api_project_id)
        if not project or project.owner_user_id != owner.id or not project.is_active or not project.is_approved:
            raise HTTPException(403, "API project is disabled")
        if not owner.api_access_enabled:
            raise HTTPException(403, "Account API access is disabled")
    else:
        # New project only; never claim an existing app, key, job or Facebook token.
        project = AuthorizedApp(
            owner_user_id=owner.id, name="Customer API", allowed_origin="",
            allowed_endpoints_json=json.dumps(sorted(available_endpoints(request))),
            is_active=True, is_approved=True,
        )
        db.add(project)
        await db.flush()
        locked.api_project_id = project.id
        owner.api_access_enabled = True
    raw, key = await generate_api_key(db, client_name=body.name,
                                     authorized_app_id=project.id, commit=False)
    locked.api_keys_unlocked = True
    await audit(db, m, "CREATE_API_KEY", str(key.id), 201)
    await db.commit()
    # This is the only response that contains the raw secret. No vault copy.
    return {**metadata(key), "secret": raw}


@router.delete("/api-keys/{identifier}")
async def revoke_customer_key(identifier: int, request: Request, response: Response,
                               m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    key = (await db.execute(select(AccessAPIKey).where(
        AccessAPIKey.id == identifier, AccessAPIKey.authorized_app_id == m.api_project_id,
    ))).scalar_one_or_none() if m.api_project_id else None
    if not key:
        raise HTTPException(404, "API key not found")
    key.is_active = False
    await audit(db, m, "REVOKE_API_KEY", str(key.id))
    await db.commit()
    return {"success": True}
