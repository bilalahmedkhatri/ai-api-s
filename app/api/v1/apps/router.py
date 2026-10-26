"""Admin management of gateway users' application grants."""

import json
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.cron import verify_cron_authorization
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.app_access_service import normalize_origin

PROTECTED_PREFIXES = ("audio", "query", "generate", "image", "video", "ingest", "media", "facebook")


def require_admin(
    authorization: str | None = Header(None),
    x_cron_secret: str | None = Header(None, alias="X-Cron-Secret"),
) -> None:
    verify_cron_authorization(authorization=authorization, x_cron_secret=x_cron_secret)


router = APIRouter(prefix="/apps", tags=["Authorized apps"], dependencies=[Depends(require_admin)])


class AppCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    owner_user_id: int = Field(gt=0)
    allowed_origin: str = Field(max_length=512)
    allowed_endpoints: list[str] = Field(default_factory=list, max_length=200)
    is_approved: bool = False

    @field_validator("allowed_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return normalize_origin(value)

    @field_validator("allowed_endpoints")
    @classmethod
    def validate_endpoints(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(r"(?:GET|POST|PUT|PATCH|DELETE|HEAD) /api/v1/(?:audio|query|generate|image|video|ingest|media|facebook)(?:/[^\s?#]*)?", value):
                raise ValueError("Use an exact METHOD /api/v1/... route template without wildcards")
            if "*" in value:
                raise ValueError("Endpoint wildcards are not allowed")
        return list(dict.fromkeys(values))


class AppUpdate(BaseModel):
    is_active: bool | None = None
    is_approved: bool | None = None
    allowed_endpoints: list[str] | None = None

    @field_validator("allowed_endpoints")
    @classmethod
    def validate_endpoints(cls, values: list[str] | None) -> list[str] | None:
        return AppCreate.validate_endpoints(values) if values is not None else None


class OwnerPermission(BaseModel):
    api_access_enabled: bool


def app_response(app: AuthorizedApp) -> dict:
    return {
        "id": app.id, "name": app.name, "owner_user_id": app.owner_user_id,
        "allowed_origin": app.allowed_origin,
        "allowed_endpoints": json.loads(app.allowed_endpoints_json),
        "is_active": app.is_active, "is_approved": app.is_approved,
    }


def available_endpoints(request: Request) -> set[str]:
    from app.services.access_policy import APP_OPERATIONS
    return {
        f"{method.upper()} {path}"
        for path, methods in request.app.openapi()["paths"].items()
        if any(path.startswith(f"/api/v1/{prefix}/") or path == f"/api/v1/{prefix}" for prefix in PROTECTED_PREFIXES)
        for method in methods
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
        and f"{method.upper()} {path}" in APP_OPERATIONS
    }


def verify_endpoints(request: Request, values: list[str]) -> None:
    if set(values) - available_endpoints(request):
        raise HTTPException(400, "One or more endpoint permissions do not match registered routes")


@router.get("/endpoints")
async def list_endpoints(request: Request):
    return sorted(available_endpoints(request))


@router.patch("/owners/{user_id}/permission")
async def set_owner_permission(user_id: int, body: OwnerPermission, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Gateway user not found")
    user.api_access_enabled = body.api_access_enabled
    await db.commit()
    return {"owner_user_id": user.id, "api_access_enabled": user.api_access_enabled}


@router.post("/", status_code=201)
async def register_app(body: AppCreate, request: Request, db: AsyncSession = Depends(get_db)):
    owner = await db.get(User, body.owner_user_id)
    if not owner or not owner.is_active:
        raise HTTPException(400, "An active gateway owner is required")
    verify_endpoints(request, body.allowed_endpoints)
    app = AuthorizedApp(
        name=body.name, owner_user_id=owner.id, allowed_origin=body.allowed_origin,
        allowed_endpoints_json=json.dumps(body.allowed_endpoints),
        is_approved=body.is_approved, is_active=True,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app_response(app)


@router.get("/")
async def list_apps(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AuthorizedApp).order_by(AuthorizedApp.id))
    return [app_response(app) for app in result.scalars().all()]


@router.patch("/{app_id}")
async def update_app(app_id: int, body: AppUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    app = await db.get(AuthorizedApp, app_id)
    if not app:
        raise HTTPException(404, "Authorized app not found")
    if body.allowed_endpoints is not None:
        verify_endpoints(request, body.allowed_endpoints)
        app.allowed_endpoints_json = json.dumps(body.allowed_endpoints)
    if body.is_active is not None:
        app.is_active = body.is_active
    if body.is_approved is not None:
        app.is_approved = body.is_approved
    await db.commit()
    return app_response(app)


@router.post("/{app_id}/link-key/{key_id}")
async def link_legacy_key(app_id: int, key_id: int, db: AsyncSession = Depends(get_db)):
    app = await db.get(AuthorizedApp, app_id)
    key = await db.get(AccessAPIKey, key_id)
    if not app or not key:
        raise HTTPException(404, "App or key not found")
    if key.authorized_app_id is not None and key.authorized_app_id != app.id:
        raise HTTPException(409, "A linked key cannot be transferred to another app")
    key.authorized_app_id = app.id
    key.allowed_domain = app.allowed_origin
    await db.commit()
    return {"key_id": key.id, "authorized_app_id": app.id}
