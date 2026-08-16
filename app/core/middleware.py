"""ASGI middleware: injects a unique X-Request-ID into every request/response."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        t0 = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        response.headers["X-Request-ID"] = request_id

        logger.info(
            "%s %s",
            request.method,
            request.url.path,
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response
