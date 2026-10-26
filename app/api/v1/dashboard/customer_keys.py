"""Session-authenticated external keys; internal execution keys stay server-only."""

from app.core.middleware import limiter
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, User
from app.services.api_key_service import generate_api_key
from app.services.customer_keys import ensure_project, locked_member
from app.services.dashboard import audit, check_origin, member, require_admin
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class CreateCustomerKey(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=2, max_length=128, pattern=r"\S")
    master_key: SecretStr | None = None  # Legacy clients may send it; never grants access.


def metadata(key):
    return {
        "id": key.id,
        "name": key.client_name,
        "prefix": key.key_prefix,
        "is_active": key.is_active,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
    }


@router.post("/members/{identifier}/api-access")
async def enable_api_access(
    identifier: int, request: Request, response: Response, m=Depends(member), db=Depends(get_db)
):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    target = await locked_member(db, identifier)
    if target.user_id:
        owner = await db.get(User, target.user_id)
        if not owner or not owner.is_active:
            raise HTTPException(403, "An active verified account is required")
        owner.api_access_enabled = True  # Explicit admin action, never implicit provisioning.
        await ensure_project(db, target)
    await audit(db, m, "ENABLE_API_KEYS", str(target.id))
    await db.commit()
    return {"success": True}


@router.get("/api-keys")
async def list_customer_keys(response: Response, m=Depends(member), db=Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    owner = await db.get(User, m.user_id)
    enabled = bool(owner and owner.is_active and owner.api_access_enabled)
    keys = (
        (
            await db.execute(
                select(AccessAPIKey)
                .where(
                    AccessAPIKey.authorized_app_id == m.api_project_id,
                    AccessAPIKey.is_internal.is_(False),
                )
                .order_by(AccessAPIKey.created_at.desc())
            )
        )
        .scalars()
        .all()
        if m.api_project_id
        else []
    )
    return {"enabled": enabled, "unlocked": enabled, "keys": [metadata(key) for key in keys]}


@router.post("/api-keys", status_code=201)
@limiter.limit("10/minute", key_func=lambda request: str(request.state.dashboard_membership.id))
async def create_customer_key(
    body: CreateCustomerKey,
    request: Request,
    response: Response,
    m=Depends(member),
    db=Depends(get_db),
):
    check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    locked = await locked_member(db, m.id)
    project = await ensure_project(db, locked)
    raw, key = await generate_api_key(db, body.name, project.id, commit=False)
    await audit(db, m, "CREATE_API_KEY", str(key.id), 201)
    await db.commit()
    return {**metadata(key), "secret": raw}


@router.delete("/api-keys/{identifier}")
async def revoke_customer_key(
    identifier: int, request: Request, response: Response, m=Depends(member), db=Depends(get_db)
):
    check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    locked = await locked_member(db, m.id)
    key = (
        (
            await db.execute(
                select(AccessAPIKey).where(
                    AccessAPIKey.id == identifier,
                    AccessAPIKey.authorized_app_id == locked.api_project_id,
                    AccessAPIKey.is_internal.is_(False),
                )
            )
        ).scalar_one_or_none()
        if locked.api_project_id
        else None
    )
    if not key:
        raise HTTPException(404, "API key not found")
    key.is_active = False
    await audit(db, m, "REVOKE_API_KEY", str(key.id))
    await db.commit()
    return {"success": True}
