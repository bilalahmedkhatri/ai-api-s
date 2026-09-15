"""app/api/v1/auth/router.py — Google OAuth2 login / callback / logout / me endpoints."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.google_auth import (
    build_google_auth_url,
    clear_auth_cookie,
    create_jwt,
    exchange_code_for_userinfo,
    generate_oauth_state,
    set_auth_cookie,
    verify_oauth_state,
)
from app.core.user_auth import get_current_user
from app.db.database import get_db
from app.models.db_models import User
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _callback_redirect_uri(request: Request) -> str:
    """Build the absolute callback URL from the current request."""
    return str(request.base_url).rstrip("/") + "/api/v1/auth/callback"


@router.get("/login", summary="Redirect to Google OAuth2 consent screen")
async def login(request: Request):
    """
    Initiates the Google OAuth2 PKCE flow.
    Redirects the browser to Google's consent screen.
    """
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth2 is not configured on this server.",
        )
    state = generate_oauth_state()
    redirect_uri = _callback_redirect_uri(request)
    auth_url = build_google_auth_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(auth_url)


@router.get("/callback", summary="Handle Google OAuth2 callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Google redirects here after user grants consent.
    Validates state (CSRF), exchanges code for user info, upserts User row,
    issues JWT cookie, then redirects to the frontend.
    """
    if error:
        logger.warning("Google OAuth2 error: %s", error)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Google OAuth error: {error}")

    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code or state parameter.")

    # Verify state CSRF token (max age 5 min)
    if not verify_oauth_state(state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OAuth state.")

    # Exchange code for Google user info
    try:
        redirect_uri = _callback_redirect_uri(request)
        userinfo = await exchange_code_for_userinfo(code=code, redirect_uri=redirect_uri)
    except Exception as exc:
        logger.error("Failed to exchange Google auth code: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to retrieve user info from Google.")

    google_id = userinfo.get("sub")
    email = userinfo.get("email")
    if not google_id or not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incomplete user info from Google.")

    # Upsert user row
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if user:
        user.name = userinfo.get("name", user.name)
        user.avatar_url = userinfo.get("picture", user.avatar_url)
        user.last_login_at = now
    else:
        user = User(
            google_id=google_id,
            email=email,
            name=userinfo.get("name"),
            avatar_url=userinfo.get("picture"),
            last_login_at=now,
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)

    # Issue JWT cookie and redirect to frontend
    token = create_jwt(user_id=user.id, email=user.email)
    response = RedirectResponse(url=settings.frontend_origin, status_code=302)
    set_auth_cookie(response, token)

    logger.info("User %s logged in via Google OAuth2", user.email)
    return response


@router.post("/logout", summary="Clear session cookie")
async def logout():
    """Invalidates the session by clearing the HttpOnly JWT cookie."""
    response = JSONResponse({"status": "logged out"})
    clear_auth_cookie(response)
    return response


@router.get("/me", summary="Get current user profile")
async def me(user: User = Depends(get_current_user)):
    """Returns the authenticated user's profile. Requires a valid session cookie."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }
