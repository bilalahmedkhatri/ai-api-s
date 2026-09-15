"""app/core/google_auth.py — Google OAuth2 helpers + JWT cookie management."""

import logging
import os
from datetime import UTC, datetime, timedelta

from fastapi import Response
from itsdangerous import BadSignature, TimestampSigner
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60
COOKIE_NAME = "session"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


# ── State CSRF protection ────────────────────────────────────────────────────

_signer = TimestampSigner(settings.secret_key)


def generate_oauth_state() -> str:
    """Return an HMAC-signed, timestamp-embedded state token for CSRF protection."""
    return _signer.sign("oauth").decode()


def verify_oauth_state(state: str, max_age: int = 300) -> bool:
    """Validate the state param. Returns False if tampered or > 5 min old."""
    try:
        _signer.unsign(state, max_age=max_age)
        return True
    except BadSignature:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_jwt(user_id: int, email: str) -> str:
    """Sign a short-lived HS256 JWT containing user identity."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on invalid signature or expiry."""
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])


# ── Cookie helpers ────────────────────────────────────────────────────────────

def set_auth_cookie(response: Response, token: str) -> None:
    """Attach a secure, HttpOnly, SameSite=Lax session cookie."""
    is_prod = not settings.debug
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,          # JS cannot read — XSS-proof
        samesite="lax",         # CSRF protection for cross-site navigation
        secure=is_prod,         # HTTPS-only in production
        max_age=JWT_EXPIRE_MINUTES * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Delete the session cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/", httponly=True, samesite="lax")


# ── Google OAuth2 URL builder ─────────────────────────────────────────────────

def build_google_auth_url(redirect_uri: str, state: str) -> str:
    """Construct the Google OAuth2 consent screen URL."""
    import urllib.parse

    params = {
        "client_id": settings.google_client_id or "",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_userinfo(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for Google user info. Returns the userinfo dict."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Exchange code for tokens
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        # 2. Fetch user profile
        access_token = token_data["access_token"]
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()
