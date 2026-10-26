"""ASGI middleware: injects a unique X-Request-ID into every request/response."""

import logging
import re
import time
import uuid
from contextlib import aclosing

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.db.database import get_db
from app.models.db_models import Query

logger = logging.getLogger(__name__)

# SlowAPI Limiter instance used across routers
limiter = Limiter(key_func=get_remote_address)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        if not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}", request_id
        ):
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        t0 = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(("/api/v1/dashboard", "/api/v1/auth")):
            response.headers["Cache-Control"] = "no-store"
        record_path = request.url.path.rstrip("/")
        if (request.method == "POST" and record_path in {"/api/v1/query", "/api/v1/generate"}
                and response.status_code >= 400 and not getattr(request.state, "query_record_id", None)):
            try:
                # Do not read rejected bodies or save keys, headers, origins or exception text.
                dependency = request.app.dependency_overrides.get(get_db, get_db)
                async with aclosing(dependency()) as sessions:
                    async for db in sessions:
                        row = Query(raw_query="", input_type="text",
                                    authorized_app_id=getattr(request.state, "audit_app_id", None))
                        row.meta = {"request_id": request_id, "status": "rejected",
                                    "http_status": response.status_code, "duration_ms": elapsed_ms,
                                    "error_type": "PreHandlerRejection", "body_omitted": True,
                                    "usage_accounting_status": "not_applicable"}
                        if record_path == "/api/v1/generate":
                            row.meta = {**row.meta, "request_type": "generate", "endpoint": "/api/v1/generate/"}
                        db.add(row)
                        await db.commit()
                        break
            except Exception as exc:
                logger.warning("Query rejection audit unavailable request_id=%s error_type=%s", request_id, type(exc).__name__)

        logger.info(
            "%s %s",
            request.method,
            request.url.path,
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "elapsed_ms": elapsed_ms,
                "status_code": response.status_code,
            },
        )
        return response
