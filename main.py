"""Application entry point — Phase 5: Sentry + deep health endpoint."""

import uvicorn
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.api.v1.router import v1_router
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.middleware import RequestIDMiddleware
from app.db.database import init_db

setup_logging()

# ── Sentry initialisation ─────────────────────────────────────────────────────
# Initialises only when SENTRY_DSN is set in .env / cloud environment.
# Captures unhandled exceptions, slow transactions, and LLM errors automatically.
_sentry_dsn = getattr(settings, "sentry_dsn", "")
if _sentry_dsn and "..." not in _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        # Capture 10 % of transactions for performance monitoring (adjust in prod).
        traces_sample_rate=0.1,
        # Don't send PII (query text may contain personal data).
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
