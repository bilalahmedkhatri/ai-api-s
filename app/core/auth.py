"""app/core/auth.py — FastAPI authentication dependencies for third-party client API keys."""

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.db_models import AccessAPIKey, AuthorizedApp
from app.services.api_key_service import verify_api_key
from app.services.app_access_service import endpoint_allowed, normalize_origin


async def get_current_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> AccessAPIKey:
    """
    FastAPI dependency to authenticate requests using third-party AccessAPIKey.
    Check exact app origin, active key, gateway owner grant, and endpoint permission.
    """
    if getattr(request.state, "api_key", None) is not None:
        return request.state.api_key
    origin_header = request.headers.get("origin") or request.headers.get("x-app-origin")
    if not origin_header:
        referer = request.headers.get("referer")
        if referer:
            try:
                origin_header = normalize_origin(referer, allow_path=True)
            except ValueError:
                pass
    if not origin_header:
        raise HTTPException(401, "Registered app origin is required")
    raw_key = None
    if x_api_key and x_api_key.strip():
        raw_key = x_api_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        raw_key = authorization.split("Bearer ", 1)[1].strip()

    if raw_key:
        api_key_record = await verify_api_key(db, raw_key=raw_key, origin_domain=origin_header)
        if not api_key_record:
            raise HTTPException(
                status_code=401,
                detail="App key, origin, approval, or gateway owner permission is invalid",
            )
        app = await db.get(AuthorizedApp, api_key_record.authorized_app_id)
        # FastAPI's lazy included routers keep the original route path relative.
        # The effective context carries its full template, including all prefixes.
        context = request.scope.get("fastapi", {}).get("effective_route_context")
        route_path = getattr(context, "path", request.scope["route"].path)
        if not app or not endpoint_allowed(app.allowed_endpoints_json, request.method, route_path):
            raise HTTPException(403, "This app does not have permission for this endpoint")
        request.state.api_key = api_key_record
        request.state.authorized_app = app
        return api_key_record

    raise HTTPException(401, "App API key is required in X-API-Key or Authorization")
