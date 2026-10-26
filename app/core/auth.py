"""app/core/auth.py — FastAPI authentication dependencies for third-party client API keys."""

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.db_models import AccessAPIKey, AuthorizedApp
from app.services.access_policy import app_membership, member_operation_allowed, operation_kind
from app.services.api_key_service import verify_api_key
from app.services.app_access_service import endpoint_allowed


async def get_current_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> AccessAPIKey:
    """
    FastAPI dependency to authenticate requests using third-party AccessAPIKey.
    Check the secret key, active account/project and endpoint permission.
    """
    if getattr(request.state, "api_key", None) is not None:
        return request.state.api_key
    raw_key = None
    if x_api_key and x_api_key.strip():
        raw_key = x_api_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        raw_key = authorization.split("Bearer ", 1)[1].strip()

    if raw_key:
        api_key_record = await verify_api_key(db, raw_key=raw_key, touch=False)
        if not api_key_record:
            raise HTTPException(
                status_code=401,
                detail="API key is invalid, revoked, or its account is disabled",
            )
        request.state.audit_app_id = api_key_record.authorized_app_id
        app = await db.get(AuthorizedApp, api_key_record.authorized_app_id)
        # FastAPI's lazy included routers keep the original route path relative.
        # The effective context carries its full template, including all prefixes.
        context = request.scope.get("fastapi", {}).get("effective_route_context")
        route_path = getattr(context, "path", request.scope["route"].path)
        if not app or not endpoint_allowed(app.allowed_endpoints_json, request.method, route_path):
            raise HTTPException(403, "This app does not have permission for this endpoint")
        if operation_kind(request.method, route_path) != "app":
            raise HTTPException(403, "This operation is not available to app keys")
        membership = await app_membership(db, app)
        if membership and not await member_operation_allowed(db, membership, request.method, route_path):
            raise HTTPException(403, "This user does not have permission for this operation")
        api_key_record.last_used_at = datetime.now(UTC)
        await db.commit()
        request.state.api_key = api_key_record
        request.state.authorized_app = app
        return api_key_record

    raise HTTPException(401, "App API key is required in X-API-Key or Authorization")
