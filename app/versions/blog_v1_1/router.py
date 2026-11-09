"""Session-protected blog management. No delete route and no gateway app registrations."""

import asyncio
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from app.blog import service
from app.blog.asset_library import list_assets
from app.blog.config import (
    APP_KEYS,
    BlogConfigurationError,
    BlogRegistry,
    BlogTarget,
    load_registry,
)
from app.blog.database import session_for_app
from app.blog.http import BlogRoute
from app.blog.migrations.schema_v3 import REVISION
from app.blog.profile import profile_data, require_complete_profile
from app.blog.storage import storage_configuration
from app.db.database import get_db
from app.models.db_models import User
from app.services.dashboard import allowed, audit, member, require
from app.versions.blog_v1_1 import media
from app.versions.blog_v1_1 import service as writes
from app.versions.blog_v1_1.access import permission_context
from app.versions.blog_v1_1.avatar import trusted_avatar, upload_avatar, verify_avatar
from app.versions.blog_v1_1.contracts import (
    AppsResponse,
    AssetListResponse,
    AvatarUploadResponse,
    CategoryEnvelope,
    CategoryInput,
    CategoryListResponse,
    ErrorResponse,
    PostCreate,
    PostEnvelope,
    PostListResponse,
    PostUpdate,
    ProfileInput,
    ProfileResponse,
    SettingsResponse,
    UploadEnvelope,
    normalized_locale,
)
from app.versions.blog_v1_1.schema import posts as v11_posts
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from fastapi.security import HTTPBearer

dashboard_bearer = HTTPBearer(
    auto_error=True,
    scheme_name="DashboardBearer",
    description="Bearer session token forwarded by the authenticated dashboard gateway.",
)


class BlogV11Route(BlogRoute):
    """Attach machine-readable permission and retry metadata to V1.1 docs."""

    def __init__(self, path, endpoint, **kwargs):
        super().__init__(path, endpoint, **kwargs)
        operation = next(iter(self.methods or {"GET"})).upper()
        if path.endswith("/apps"):
            permissions = ["page:blog-editor"]
        elif path.endswith("/profile"):
            permissions = ["page:blog-editor"]
        elif path.endswith("/profile/avatar"):
            permissions = ["page:blog-editor"]
        else:
            permissions = ["page:blog-editor", "component:blog.app.{app_key}"]
            if operation == "POST" and path.endswith("/categories"):
                permissions.append("component:blog.create OR component:blog.update")
            elif operation == "POST" and path.endswith("/posts"):
                permissions.append("component:blog.create")
            elif operation == "PATCH" and "/posts/" in path:
                permissions.append("component:blog.update")
            elif operation == "POST" and path.endswith("/assets"):
                permissions.append("component:blog.create OR component:blog.update")
        self.openapi_extra = {
            **(self.openapi_extra or {}),
            "x-required-permissions": permissions,
            "x-retryable-statuses": [409, 429, 503],
            "x-side-effects": "No side effect on validation, authorization, conflict, or storage failure.",
        }
COMMON_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication is missing or expired."},
    403: {"model": ErrorResponse, "description": "The current member lacks the required grant."},
    404: {"model": ErrorResponse, "description": "The app or owned post was not found."},
    409: {"model": ErrorResponse, "description": "The request conflicts with current state; reload before retrying."},
    413: {"model": ErrorResponse, "description": "The request or upload exceeds its limit."},
    415: {"model": ErrorResponse, "description": "The media or request content type is unsupported."},
    422: {"model": ErrorResponse, "description": "Input validation or asset ownership failed."},
    429: {"model": ErrorResponse, "description": "Upload capacity is temporarily exhausted; retry later."},
    503: {"model": ErrorResponse, "description": "Blog database, migration, or media storage is unavailable; retry safely."},
}
router = APIRouter(
    prefix="/dashboard/blog",
    tags=["dashboard"],
    route_class=BlogV11Route,
    dependencies=[Depends(dashboard_bearer)],
    responses=COMMON_RESPONSES,
)
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
    permissions: object


def access_dependency(operation=None):
    async def access(
        app_key: str = Path(max_length=40),
        permissions=Depends(permission_context),
        central=Depends(get_db),
        registry=Depends(registry_dependency),
    ):
        membership = permissions.membership
        permissions.require("page", "blog-editor")
        if app_key not in APP_KEYS:
            raise HTTPException(404, "Unknown blog app.")
        permissions.require("component", f"blog.app.{app_key}")
        if operation == "category":
            if not (
                permissions.allows("component", "blog.create")
                or permissions.allows("component", "blog.update")
            ):
                raise HTTPException(403, "Blog create or update permission is required.")
        elif operation:
            permissions.require("component", f"blog.{operation}")
        try:
            target = registry.for_app(app_key)
        except BlogConfigurationError:
            raise HTTPException(503, "Blog database is not configured for this app.") from None
        return BlogAccess(app_key, target, registry, membership, central, permissions)

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
        scope.permissions.require("component", "blog.publish")
    if requested_status == "published" or (
        existing_status == "published" and requested_status is None
    ):
        require_complete_profile(scope.membership)


@router.get("/apps", response_model=AppsResponse)
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


@router.get("/profile", response_model=ProfileResponse)
async def profile(membership=Depends(member), central=Depends(get_db)):
    await require(central, membership, "page", "blog-editor")
    return await profile_data(central, membership)


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(body: ProfileInput, membership=Depends(member), central=Depends(get_db)):
    await require(central, membership, "page", "blog-editor")
    if "avatar_upload_token" in body.model_fields_set and "avatar_url" not in body.model_fields_set:
        raise HTTPException(422, "Provide the uploaded avatar URL with its receipt.")
    if "avatar_url" in body.model_fields_set and body.avatar_url:
        verify_avatar(body.avatar_url, body.avatar_upload_token, membership.id)
    for field in body.model_fields_set - {"avatar_upload_token"}:
        setattr(membership, field, getattr(body, field))
    if "name" in body.model_fields_set:
        owner = await central.get(User, membership.user_id)
        if owner is not None:
            owner.name = membership.name
    await audit(central, membership, "BLOG_PROFILE_UPDATE", str(membership.id))
    await central.commit()
    return await profile_data(central, membership)


@router.post("/profile/avatar", status_code=201, response_model=AvatarUploadResponse)
async def upload_profile_avatar(
    request: Request,
    membership=Depends(member),
    central=Depends(get_db),
):
    await require(central, membership, "page", "blog-editor")
    return await upload_avatar(request, membership, storage_configuration())


@router.get("/apps/{app_key}/settings", response_model=SettingsResponse)
async def settings(scope=Depends(read_access)):
    return {
        "key": scope.key,
        "label": scope.target.label,
        "public_blog_url": scope.target.public_blog_url,
        "publishing_configured": bool(scope.target.public_blog_url),
    }


@router.get("/apps/{app_key}/categories", response_model=CategoryListResponse)
async def categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=128),
    db=Depends(read_database),
):
    return await service.list_categories(db, page=page, page_size=page_size, search=search)


@router.post("/apps/{app_key}/categories", status_code=201, response_model=CategoryEnvelope)
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


@router.get("/apps/{app_key}/posts", response_model=PostListResponse)
async def posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Literal["draft", "published", "archived"] | None = None,
    locale: str | None = Query(None, max_length=35),
    search: str | None = Query(None, max_length=200),
    scope=Depends(read_access),
    db=Depends(read_database),
):
    if locale is not None:
        try:
            locale = normalized_locale(locale)
        except ValueError:
            raise HTTPException(422, "Invalid locale.") from None
    return await service.list_posts(
        db,
        page=page,
        page_size=page_size,
        status=status,
        locale=locale,
        search=search,
        ownership=await service.owned_posts(scope.central, scope.key, scope.membership.id),
        admin=scope.membership.role == "admin",
    )


@router.get("/apps/{app_key}/posts/{identifier}", response_model=PostEnvelope)
async def post(
    identifier: int = Path(gt=0, le=2147483647),
    scope=Depends(read_access),
    db=Depends(read_database),
):
    return {"post": await find_owned_post(db, identifier, scope)}


@router.post("/apps/{app_key}/posts", status_code=201, response_model=PostEnvelope)
async def create_post(body: PostCreate, scope=Depends(create_access), db=Depends(create_database)):
    await publish_permission(scope, None, body.status)
    snapshot = await service.author_snapshot(scope.central, scope.membership)
    snapshot = verified_author(snapshot, scope.membership.id)
    row = await writes.create_post(
        db,
        scope.target,
        body,
        snapshot,
        creator_membership_id=scope.membership.id,
        uploader_id=None if scope.membership.role == "admin" else scope.membership.id,
    )
    await db.commit()
    recorded = await service.record_audit(
        scope.central, scope.membership, "BLOG_POST_CREATE", f"{scope.key}:post:{row['id']}"
    )
    return {"post": row, "audit_recorded": recorded}


@router.patch("/apps/{app_key}/posts/{identifier}", response_model=PostEnvelope)
async def update_post(
    body: PostUpdate,
    identifier: int = Path(gt=0, le=2147483647),
    scope=Depends(update_access),
    db=Depends(update_database),
):
    existing = await find_owned_post(db, identifier, scope)
    if existing["version"] != body.version:
        raise HTTPException(409, "This post changed. Reload it before saving.")
    await publish_permission(scope, existing["status"], body.status)
    author = await service.original_author_snapshot(scope.central, scope.key, existing)
    try:
        if author is not None:
            creator_id = (existing.get("metadata_json") or {}).get("created_by_membership_id")
            author = verified_author(author, creator_id)
        row = await writes.update_post(
            db,
            scope.target,
            identifier,
            body,
            existing,
            author=author,
            uploader_id=None if scope.membership.role == "admin" else scope.membership.id,
            allow_locale_change=scope.membership.role == "admin",
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            service.logger.warning(
                "Blog update conflict app=%s post_id=%s "
                "request_version=%s loaded_version=%s reason=%s",
                scope.key,
                identifier,
                body.version,
                existing["version"],
                exc.detail,
            )
        raise

    await db.commit()
    recorded = await service.record_audit(
        scope.central, scope.membership, "BLOG_POST_UPDATE", f"{scope.key}:post:{identifier}"
    )
    return {"post": row, "audit_recorded": recorded}


media_database = session_dependency(category_access, timeout=150)


def storage_dependency():
    return storage_configuration()


@router.post("/apps/{app_key}/assets", status_code=201, response_model=UploadEnvelope)
async def upload_asset(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    title: str = Query(min_length=1, max_length=70),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=36, max_length=36),
    scope=Depends(category_access),
    db=Depends(media_database),
    storage=Depends(storage_dependency),
):
    return await media.upload(request, scope, db, storage, filename, idempotency_key, title)


@router.get("/apps/{app_key}/assets", response_model=AssetListResponse)
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


async def find_owned_post(db, identifier, scope):
    row = (
        (await db.execute(sa.select(v11_posts).where(v11_posts.c.id == identifier)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Blog post not found.")
    row = dict(row)
    if scope.membership.role != "admin":
        owner = (row.get("metadata_json") or {}).get("created_by_membership_id")
        if owner is None:
            from app.models.dashboard import AuditLog

            owners = (
                (
                    await scope.central.execute(
                        sa.select(AuditLog.actor_membership_id)
                        .where(
                            AuditLog.action == "BLOG_POST_CREATE",
                            AuditLog.target == f"{scope.key}:post:{identifier}",
                            AuditLog.status.between(200, 299),
                            AuditLog.actor_membership_id.is_not(None),
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            owner = owners[0] if len(owners) == 1 else None
        if type(owner) is not int or owner != scope.membership.id:
            raise HTTPException(404, "Blog post not found.")
    return row


def verified_author(snapshot, creator_id):
    snapshot = dict(snapshot)
    url = snapshot.get("author_avatar_url")
    if url and not trusted_avatar(url, creator_id, storage_configuration()):
        raise HTTPException(422, "Upload and save the author's profile image through V1.1 first.")
    return snapshot
