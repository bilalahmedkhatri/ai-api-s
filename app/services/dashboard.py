"""Shared dashboard authorization, vault, and verified identity helpers."""

import hashlib
import hmac
import json
import os
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.blog.config import APP_KEYS as BLOG_APP_KEYS
from app.core.config import settings
from app.db.database import get_db
from app.models.dashboard import AuditLog, DashboardSession, EmailChallenge, Membership, Permission
from app.models.db_models import User

PAGES = ["overview", "endpoints", "activity", "users", "settings", "blog-editor"]
COMPONENTS = [
    "endpoint.execute",
    "endpoint.create",
    "endpoint.update",
    "endpoint.delete",
    "credentials.manage",
    "members.manage",
    "blog.create",
    "blog.update",
    "blog.publish",
] + [f"blog.app.{key}" for key in BLOG_APP_KEYS]


def now():
    return datetime.now(UTC)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def dashboard_origin():
    return os.environ.get("DASHBOARD_ORIGIN", "http://localhost:3002").rstrip("/")


def check_origin(request):
    if request.headers.get("origin") != dashboard_origin():
        raise HTTPException(403, "Invalid request origin")


def cipher():
    if not settings.encryption_key:
        raise HTTPException(503, "Backend credential encryption is not configured")
    return Fernet(settings.encryption_key.encode())


def redact(value, sensitive_values=()):
    if isinstance(value, list):
        return [redact(v, sensitive_values) for v in value]
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if any(
                s in k.lower()
                for s in (
                    "secret",
                    "password",
                    "token",
                    "raw_key",
                    "master_key",
                    "plain_key",
                    "encrypted",
                    "authorization",
                    "cookie",
                    "api_key",
                )
            )
            else redact(v, sensitive_values)
            for k, v in value.items()
        }
    if isinstance(value, str):
        for secret in sensitive_values:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


async def member(request: Request, db=Depends(get_db)):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    session = (
        await db.execute(
            select(DashboardSession).where(
                DashboardSession.token_hash == digest(token), DashboardSession.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not session or aware(session.expires_at) <= now():
        raise HTTPException(401, "Dashboard session expired")
    m = await db.get(Membership, session.membership_id)
    owner = await db.get(User, m.user_id) if m and m.user_id else None
    if (
        not m
        or m.status != "approved"
        or not owner
        or not owner.is_active
        or (m.access_expires_at and aware(m.access_expires_at) <= now())
    ):
        raise HTTPException(403, "Dashboard access is not approved")
    request.state.dashboard_membership = m
    return m


async def grants(db, m):
    return (
        (await db.execute(select(Permission).where(Permission.membership_id == m.id)))
        .scalars()
        .all()
    )


async def allowed(db, m, kind, key, action="view"):
    if m.role == "admin" or (kind == "page" and key == "api-keys"):
        return True
    return any(
        p.resource_type == kind and p.resource_key == key and p.action == action
        for p in await grants(db, m)
    )


async def require(db, m, kind, key, action="view"):
    if not await allowed(db, m, kind, key, action):
        raise HTTPException(403, "Permission denied")


def require_admin(m):
    if m.role != "admin":
        raise HTTPException(403, "Administrator permission required")


async def audit(db, m, action, target, status=200, elapsed_ms=None):
    db.add(
        AuditLog(
            actor_membership_id=m.id if m else None,
            action=action,
            target=target,
            status=status,
            elapsed_ms=elapsed_ms,
            created_at=now(),
        )
    )


async def dto(db, m):
    rules = await grants(db, m)

    def values(kind):
        return [p.resource_key for p in rules if p.resource_type == kind]

    return {
        "id": m.id,
        "name": m.name or m.email,
        "designation": m.designation,
        "email": m.email,
        "role": m.role,
        "approved": int(m.status == "approved"),
        "active": int(m.status != "revoked"),
        "status": m.status,
        "created_at": m.created_at.isoformat(),
        "access_expires_at": m.access_expires_at,
        "permission_version": m.permission_version,
        "grants": json.dumps(values("endpoint")),
        "page_permissions": json.dumps(values("page")),
        "sections": values("section"),
        "components": values("component"),
        "credential_ids": json.dumps([int(v) for v in values("credential")]),
        "permissions": [
            {"resource_type": p.resource_type, "resource_key": p.resource_key, "action": p.action}
            for p in rules
        ],
    }


async def establish(db, identity):
    email = identity["email"].lower()
    m = (await db.execute(select(Membership).where(Membership.email == email)
                          .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if (
        not m
        or m.status != "approved"
        or (m.access_expires_at and aware(m.access_expires_at) <= now())
    ):
        raise HTTPException(403, "Dashboard access has not been approved")
    user = (
        await db.execute(select(User).where(User.google_id == identity["sub"]))
    ).scalar_one_or_none()
    if m.user_id and (not user or user.id != m.user_id):
        raise HTTPException(403, "Google identity does not match the approved membership")
    if not user:
        existing_email = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing_email:
            raise HTTPException(403, "Existing gateway account uses another Google identity")
        user = User(google_id=identity["sub"], email=email, name=identity.get("name"), api_access_enabled=True)
        db.add(user)
        await db.flush()
    if not user.is_active:
        raise HTTPException(403, "Gateway account is deactivated")
    m.user_id = user.id
    if user.api_access_enabled:
        from app.services.customer_keys import ensure_project
        await ensure_project(db, m)
    user.last_login_at = now()
    token = secrets.token_urlsafe(48)
    db.add(
        DashboardSession(
            token_hash=digest(token),
            membership_id=m.id,
            created_at=now(),
            expires_at=now() + timedelta(hours=8),
        )
    )
    await audit(db, m, "SIGN_IN", "dashboard")
    await db.commit()
    return {"session_token": token, "user": await dto(db, m)}


async def email_challenge(db, email, purpose, identity=None):
    recent = (
        (
            await db.execute(
                select(EmailChallenge).where(
                    EmailChallenge.email == email,
                    EmailChallenge.expires_at > now() + timedelta(minutes=9),
                )
            )
        )
        .scalars()
        .all()
    )
    if recent:
        raise HTTPException(429, "Wait before requesting another verification code")
    if not os.environ.get("SMTP_HOST") or not os.environ.get("SMTP_FROM"):
        raise HTTPException(503, "Email verification delivery is not configured")
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1000000):06d}"
    hashed = hmac.new(
        settings.secret_key.encode(), f"{token}:{code}".encode(), hashlib.sha256
    ).hexdigest()
    row = EmailChallenge(
        token_hash=digest(token),
        email=email,
        challenge_hash=hashed,
        purpose=purpose,
        identity_json=json.dumps(identity) if identity else None,
        expires_at=now() + timedelta(minutes=10),
    )
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = email
    message["Subject"] = "AI Gateway email verification"
    message.set_content(f"Your verification code is {code}. It expires in 10 minutes.")

    def deliver():
        with smtplib.SMTP(
            os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=15
        ) as smtp:
            if os.environ.get("SMTP_TLS", "true").lower() != "false":
                smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)

    import asyncio

    try:
        await asyncio.to_thread(deliver)
    except Exception:
        raise HTTPException(503, "Unable to deliver verification email") from None
    db.add(row)
    await db.commit()
    return {"requires_email_verification": True, "verification_token": token}
