"""Session-protected blog management. No delete route and no gateway app registrations."""

import asyncio
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response

from app.blog import media, service
from app.blog.asset_library import list_assets
from app.blog.avatar import upload_avatar
from app.blog.config import (
    APP_KEYS,
    BlogConfigurationError,
    BlogRegistry,
    BlogTarget,
    load_registry,
)
from app.blog.contracts import (
    CategoryInput,
    PostCreate,
    PostUpdate,
    ProfileInput,
    normalized_locale,
)
from app.blog.database import session_for_app
from app.blog.http import BlogRoute
from app.blog.migrations.schema_v3 import REVISION
from app.blog.profile import profile_data, require_complete_profile
from app.blog.storage import storage_configuration
from app.db.database import get_db
from app.models.db_models import User
from app.services.dashboard import allowed, audit, member, require

router = APIRouter(prefix="/dashboard/blog", tags=["dashboard"], route_class=BlogRoute)
version_table = sa.Table(
    "blog_schema_version", sa.MetaData(), sa.Column("version_num", sa.String(32)), schema="public"
)


def registry_dependency():
    try:
        return load_registry()
    except BlogConfigurationError:
        raise HTTPException(503, "Blog storage configuration is invalid.") from None


@dataclass
class BlogAccess:
    key: str
    target: BlogTarget
    registry: BlogRegistry
    membership: object
    central: object


def access_dependency(operation=None):
    async def access(
        app_key: str = Path(max_length=40),
        membership=Depends(member),
        central=Depends(get_db),
        registry=Depends(registry_dependency),
    ):
        await require(central, membership, "page", "blog-editor")
        if app_key not in APP_KEYS:
            raise HTTPException(404, "Unknown blog app.")
        await require(central, membership, "component", f"blog.app.{app_key}")
        if operation == "category":
            if not (
                await allowed(central, membership, "component", "blog.create")
                or await allowed(central, membership, "component", "blog.update")
            ):
                raise HTTPException(403, "Blog create or update permission is required.")
        elif operation:
            await require(central, membership, "component", f"blog.{operation}")
        try:
            target = registry.for_app(app_key)
        except BlogConfigurationError:
            raise HTTPException(503, "Blog database is not configured for this app.") from None
        return BlogAccess(app_key, target, registry, membership, central)

    return access


read_access = access_dependency()
create_access = access_dependency("create")
update_access = access_dependency("update")
category_access = access_dependency("category")


def session_dependency(access, timeout=35):
    async def database(request: Request, scope=Depends(access)):
        async with asyncio.timeout(timeout):
            async with session_for_app(
                scope.registry, scope.key, getattr(request.app.state, "blog_engines", None)
            ) as session:
                revision = (
                    await session.execute(sa.select(version_table.c.version_num))
                ).scalar_one_or_none()
                if revision != REVISION:
                    raise HTTPException(503, "The selected app needs its blog schema migration.")
                yield session

    return database


read_database = session_dependency(read_access)
create_database = session_dependency(create_access)
update_database = session_dependency(update_access)
category_database = session_dependency(category_access)


async def publish_permission(scope, existing_status, requested_status):
    if existing_status == "published" or requested_status == "published":
        await require(scope.central, scope.membership, "component", "blog.publish")
    if requested_status == "published" or (
        existing_status == "published" and requested_status is None
    ):
        require_complete_profile(scope.membership)


@router.get("/apps")
async def blog_apps(
    response: Response,
    membership=Depends(member),
    db=Depends(get_db),
    registry=Depends(registry_dependency),
):
    response.headers["Cache-Control"] = "no-store"
    await require(db, membership, "page", "blog-editor")
    apps = []
    for target in registry.targets:
        if await allowed(db, membership, "component", f"blog.app.{target.key}"):
            apps.append({"key": target.key, "label": target.label, "configured": target.ready})
    return {"apps": apps}


@router.get("/profile")
async def profile(membership=Depends(member), central=Depends(get_db)):
    await require(central, membership, "page", "blog-editor")
    return await profile_data(central, membership)


@router.patch("/profile")
async def update_profile(body: ProfileInput, membership=Depends(member), central=Depends(get_db)):
    await require(central, membership, "page", "blog-editor")
    for field in body.model_fields_set:
        setattr(membership, field, getattr(body, field))
    if "name" in body.model_fields_set:
        owner = await central.get(User, membership.user_id)
        if owner is not None:
            owner.name = membership.name
    await audit(central, membership, "BLOG_PROFILE_UPDATE", str(membership.id))
    await central.commit()
    return await profile_data(central, membership)


@router.post("/profile/avatar", status_code=201)
async def upload_profile_avatar(
    request: Request,
    membership=Depends(member),
    central=Depends(get_db),
):
    await require(central, membership, "page", "blog-editor")
    return await upload_avatar(request, membership, storage_configuration())


@router.get("/apps/{app_key}/settings")
async def settings(scope=Depends(read_access)):
    return {
        "key": scope.key,
        "label": scope.target.label,
        "public_blog_url": scope.target.public_blog_url,
        "publishing_configured": bool(scope.target.public_blog_url),
    }


@router.get("/apps/{app_key}/categories")
async def categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=128),
    db=Depends(read_database),
):
    return await service.list_categories(db, page=page, page_size=page_size, search=search)


@router.post("/apps/{app_key}/categories", status_code=201)
async def create_category(
    body: CategoryInput,
    response: Response,
    scope=Depends(category_access),
    db=Depends(category_database),
):
    row, created = await service.category_for_name(db, body.name)
    await db.commit()
    recorded = await service.record_audit(
        scope.central,
        scope.membership,
        "BLOG_CATEGORY_CREATE" if created else "BLOG_CATEGORY_REUSE",
        f"{scope.key}:category:{row['id']}",
    )
    response.status_code = 201 if created else 200
    return {"category": row, "created": created, "audit_recorded": recorded}


@router.get("/apps/{app_key}/posts")
async def posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Literal["draft", "published", "archived"] | None = None,
    locale: str | None = Query(None, max_length=35),
    search: str | None = Query(None, max_length=200),
    db=Depends(read_database),
):
    if locale is not None:
        try:
            locale = normalized_locale(locale)
        except ValueError:
            raise HTTPException(422, "Invalid locale.") from None
    return await service.list_posts(
        db, page=page, page_size=page_size, status=status, locale=locale, search=search
    )


@router.get("/apps/{app_key}/posts/{identifier}")
async def post(identifier: int = Path(gt=0, le=2147483647), db=Depends(read_database)):
    return {"post": await service.find_post(db, identifier)}


@router.post("/apps/{app_key}/posts", status_code=201)
async def create_post(body: PostCreate, scope=Depends(create_access), db=Depends(create_database)):
    await publish_permission(scope, None, body.status)
    snapshot = await service.author_snapshot(scope.central, scope.membership)
    row = await service.create_post(db, scope.target, body, snapshot)
    await db.commit()
    recorded = await service.record_audit(
        scope.central, scope.membership, "BLOG_POST_CREATE", f"{scope.key}:post:{row['id']}"
    )
    return {"post": row, "audit_recorded": recorded}


@router.patch("/apps/{app_key}/posts/{identifier}")
async def update_post(
    body: PostUpdate,
    identifier: int = Path(gt=0, le=2147483647),
    scope=Depends(update_access),
    db=Depends(update_database),
):
    existing = await service.find_post(db, identifier)
    await publish_permission(scope, existing["status"], body.status)
    row = await service.update_post(db, scope.target, identifier, body, existing)
    await db.commit()
    recorded = await service.record_audit(
        scope.central, scope.membership, "BLOG_POST_UPDATE", f"{scope.key}:post:{identifier}"
    )
    return {"post": row, "audit_recorded": recorded}


media_database = session_dependency(category_access, timeout=150)


def storage_dependency():
    return storage_configuration()


@router.post("/apps/{app_key}/assets", status_code=201)
async def upload_asset(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=36, max_length=36),
    scope=Depends(category_access),
    db=Depends(media_database),
    storage=Depends(storage_dependency),
):
    return await media.upload(request, scope, db, storage, filename, idempotency_key)


@router.get("/apps/{app_key}/assets")
async def assets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    media_type: Literal["image"] | None = None,
    scope=Depends(read_access),
    db=Depends(read_database),
):
    return await list_assets(
        db, scope.membership.id, page=page, page_size=page_size, media_type=media_type
    )
