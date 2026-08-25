"""app/core/auth.py — FastAPI authentication dependencies for third-party client API keys."""

import logging
from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.database import get_db
from app.models.db_models import AccessAPIKey
from app.services.api_key_service import verify_api_key

logger = logging.getLogger(__name__)


async def get_current_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    query_api_key: str | None = Query(default=None, alias="api_key"),
) -> AccessAPIKey | None:
    """
    FastAPI dependency to authenticate requests using third-party AccessAPIKey.
    Checks X-API-Key header, Authorization Bearer header, or api_key query param.
    """
    raw_key = None
    if x_api_key and x_api_key.strip():
        raw_key = x_api_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        raw_key = authorization.split("Bearer ", 1)[1].strip()
    elif query_api_key and query_api_key.strip():
        raw_key = query_api_key.strip()

    # Extract origin/referer domain from request headers
    origin_header = request.headers.get("origin") or request.headers.get("referer")

    if raw_key:
        api_key_record = await verify_api_key(db, raw_key=raw_key, origin_domain=origin_header)
        if not api_key_record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid, inactive, or domain-mismatched AccessAPIKey",
            )
        # Store authenticated key record in request state
        request.state.api_key = api_key_record
        return api_key_record

    # If no key provided
    if settings.require_api_key_for_query:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required API key. Pass via 'X-API-Key' header or 'Authorization: Bearer <key>'",
        )

    return None
