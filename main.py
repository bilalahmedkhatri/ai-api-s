"""Application entry point — Phase 5: Sentry + deep health endpoint."""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
import uvicorn
from app.api.v1.router import v1_router
from app.core.app_cors import AppCORSMiddleware
from app.core.config import settings
from app.core.logging_config import (
    _JsonlBatchHandler,
    archive_log_loop,
    setup_logging,
    upload_log_batches,
)
from app.core.middleware import RequestIDMiddleware, limiter
from app.services.tts.maintenance import maintenance_loop
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import _rate_limit_exceeded_handler

setup_logging()
logger = logging.getLogger(__name__)

# ── Sentry initialisation ─────────────────────────────────────────────────────
_sentry_dsn = getattr(settings, "sentry_dsn", "")
if _sentry_dsn and "..." not in _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,
        send_default_pii=False,
        environment="production" if not settings.debug else "development",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    archive_task = asyncio.create_task(archive_log_loop())
    maintenance_task = asyncio.create_task(maintenance_loop())
    try:
        yield
    finally:
        archive_task.cancel()
        maintenance_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await archive_task
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance_task
        for handler in logging.getLogger().handlers:
            if isinstance(handler, _JsonlBatchHandler):
                handler.seal()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(upload_log_batches(), timeout=10)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

os.makedirs(settings.generated_media_dir, exist_ok=True)
app.mount("/static/generated", StaticFiles(directory=settings.generated_media_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AppCORSMiddleware)
app.add_middleware(RequestIDMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log validation details on 422 Unprocessable Content to ease debugging."""
    logger.warning(
        "Request validation error on %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
        extra={"request_id": getattr(request.state, "request_id", None)},
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )


app.include_router(v1_router)


# ── Shallow health (load-balancer / Docker healthcheck) ───────────────────────
@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=settings.debug,
    )
