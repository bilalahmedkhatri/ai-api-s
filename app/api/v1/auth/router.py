"""app/api/v1/auth/router.py — Google OAuth2 login / callback / logout / me endpoints."""

import base64
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.models.dashboard import OAuthTransaction
from app.models.db_models import User
from app.services.dashboard import cipher, digest, now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _callback_redirect_uri(request: Request) -> str:
    """Build the absolute callback URL from the current request."""
    return str(request.base_url).rstrip("/") + "/api/v1/auth/callback"


@router.get("/login", summary="Redirect to Google OAuth2 consent screen")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
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
    binding = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    db.add(OAuthTransaction(state_hash=digest(state), browser_binding_hash=digest(binding),
                           nonce="legacy", pkce_verifier=cipher().encrypt(verifier.encode()).decode(),
                           expires_at=now() + timedelta(minutes=5)))
    await db.commit()
    redirect_uri = _callback_redirect_uri(request)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    auth_url = build_google_auth_url(redirect_uri=redirect_uri, state=state, code_challenge=challenge)
    response = RedirectResponse(auth_url, headers={"Cache-Control": "no-store"})
    response.set_cookie("gateway_oauth_binding", binding, httponly=True, samesite="lax",
                        secure=not settings.debug, max_age=300, path="/api/v1/auth")
    return response


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
    if state and state.startswith("dashboard."):
        from app.api.v1.dashboard.router import oauth_callback
        return await oauth_callback(request, code, state, error, db)

    if error:
        logger.warning("Google OAuth2 authorization failed")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google authorization failed")

    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code or state parameter.")

    # Verify state CSRF token (max age 5 min)
    if not verify_oauth_state(state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OAuth state.")
    claimed = (await db.execute(update(OAuthTransaction).where(
        OAuthTransaction.state_hash == digest(state),
        OAuthTransaction.browser_binding_hash == digest(request.cookies.get("gateway_oauth_binding", "")),
        OAuthTransaction.consumed_at.is_(None), OAuthTransaction.expires_at > now(),
    ).values(consumed_at=now()).returning(OAuthTransaction.id))).scalar_one_or_none()
    await db.commit()
    if not claimed:
        raise HTTPException(400, "Invalid or expired browser login")
    transaction = await db.get(OAuthTransaction, claimed)

    # Exchange code for Google user info
    try:
        redirect_uri = _callback_redirect_uri(request)
        verifier = cipher().decrypt(transaction.pkce_verifier.encode()).decode()
        userinfo = await exchange_code_for_userinfo(code=code, redirect_uri=redirect_uri, code_verifier=verifier)
    except Exception as exc:
        logger.warning("Google exchange failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to retrieve user info from Google.") from None

    google_id = userinfo.get("sub")
    email = userinfo.get("email")
    if not google_id or not email or userinfo.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incomplete user info from Google.")

    # Upsert user row
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    login_time = datetime.now(UTC)
    if user:
        if not user.is_active:
            raise HTTPException(403, "Account is deactivated")
        user.name = userinfo.get("name", user.name)
        user.avatar_url = userinfo.get("picture", user.avatar_url)
        user.last_login_at = login_time
    else:
        user = User(
            google_id=google_id,
            email=email,
            name=userinfo.get("name"),
            avatar_url=userinfo.get("picture"),
            last_login_at=login_time,
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)

    # Issue JWT cookie and redirect to frontend
    token = create_jwt(user_id=user.id, email=user.email, session_version=user.session_version)
    response = RedirectResponse(url=settings.frontend_origin, status_code=302)
    set_auth_cookie(response, token)
    response.delete_cookie("gateway_oauth_binding", path="/api/v1/auth")

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
