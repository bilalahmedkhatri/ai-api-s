"""Application entry point — Phase 5: Sentry + deep health endpoint."""

import logging
import uvicorn
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.api.v1.router import v1_router
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.middleware import RequestIDMiddleware
from app.db.database import init_db

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
    await init_db()
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)


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
