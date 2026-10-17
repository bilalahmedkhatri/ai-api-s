"""App CORS uses approved gateway registrations, independent of admin/OAuth CORS."""

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.db.database import AsyncSessionLocal
from app.models.db_models import AuthorizedApp, User
from app.services.app_access_service import normalize_origin

APP_PREFIXES = ("audio", "query", "generate", "image", "video", "ingest", "media", "facebook")
ALLOWED_HEADERS = {"authorization", "content-type", "x-api-key", "x-app-origin", "cache-control"}


class AppCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        protected = any(path == f"/api/v1/{name}" or path.startswith(f"/api/v1/{name}/") for name in APP_PREFIXES)
        origin = request.headers.get("origin")
        if not protected or not origin:
            return await call_next(request)
        try:
            normalized = normalize_origin(origin)
        except ValueError:
            if request.method == "OPTIONS":
                return JSONResponse({"detail": "Invalid app origin"}, status_code=403)
            return await call_next(request)
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            requested_method = request.headers["access-control-request-method"].upper()
            requested_headers = {h.strip().lower() for h in request.headers.get("access-control-request-headers", "").split(",") if h.strip()}
            if requested_method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"} or requested_headers - ALLOWED_HEADERS:
                return JSONResponse({"detail": "Unsupported preflight"}, status_code=403)
            try:
                async with AsyncSessionLocal() as db:
                    registered = (await db.execute(select(AuthorizedApp.id).join(User, AuthorizedApp.owner_user_id == User.id).where(
                        AuthorizedApp.allowed_origin == normalized,
                        AuthorizedApp.is_active.is_(True), AuthorizedApp.is_approved.is_(True),
                        User.is_active.is_(True), User.api_access_enabled.is_(True),
                    ).limit(1))).scalar_one_or_none()
            except Exception:
                return JSONResponse({"detail": "App authorization unavailable"}, status_code=503)
            if not registered:
                return JSONResponse({"detail": "App origin is not approved"}, status_code=403)
            response = Response(status_code=204)
            response.headers["access-control-allow-methods"] = requested_method
            response.headers["access-control-allow-headers"] = ", ".join(sorted(ALLOWED_HEADERS))
        else:
            response = await call_next(request)
            app = getattr(request.state, "authorized_app", None)
            if not app or app.allowed_origin != normalized:
                return response
        response.headers["access-control-allow-origin"] = origin
        response.headers["access-control-allow-credentials"] = "true"
        response.headers["vary"] = ", ".join(filter(None, [response.headers.get("vary"), "Origin"]))
        return response
