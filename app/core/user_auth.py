"""app/core/user_auth.py — FastAPI dependency for JWT cookie authentication."""

import logging

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.google_auth import COOKIE_NAME, decode_jwt
from app.db.database import get_db
from app.models.db_models import User

logger = logging.getLogger(__name__)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency that validates the session JWT cookie and returns the User row.
    Raises HTTP 401 if the cookie is missing, expired, or the user is inactive.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in via /api/v1/auth/login",
        )
    try:
        payload = decode_jwt(token)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
        )

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or deactivated.",
        )
    return user


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Like get_current_user but returns None instead of raising 401.
    Used on endpoints that accept both authenticated users and API key clients.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_jwt(token)
        user_id = int(payload["sub"])
        user = await db.get(User, user_id)
        return user if (user and user.is_active) else None
    except Exception:
        return None
