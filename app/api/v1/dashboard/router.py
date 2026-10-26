"""Backend-owned dashboard authentication, authorization and management API."""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import httpx
from app.core.config import settings
from app.core.middleware import limiter
from app.db.database import get_db
from app.models.dashboard import (
    AccessRequest,
    AuditLog,
    DashboardSession,
    EmailChallenge,
    Membership,
    OAuthTransaction,
    Permission,
    ServiceCredential,
)
from app.services.access_policy import member_operation_allowed, operation_kind
from app.services.dashboard import (
    COMPONENTS,
    PAGES,
    allowed,
    audit,
    aware,
    check_origin,
    cipher,
    dashboard_origin,
    digest,
    dto,
    email_challenge,
    establish,
    member,
    now,
    redact,
    require,
    require_admin,
)
from app.services.member_lifecycle import (
    invalidate_member,
    lock_admin_change,
    lock_target,
    protect_administrator,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Query as QueryParam
from fastapi.responses import RedirectResponse, Response
from jose import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def history_filters(
    response: Response, m=Depends(member),
    request_type: str | None = QueryParam(None, max_length=20, description="query, generate, tts, image or video; omit for all"),
    status: str | None = QueryParam(None, max_length=20, description="queued, processing, completed, failed, cancelled, rejected or unknown"),
    model: str | None = QueryParam(None, min_length=1, max_length=128),
    provider: str | None = QueryParam(None, min_length=1, max_length=64),
    search: str | None = QueryParam(None, min_length=1, max_length=128, description="Literal substring of request ID, model or provider"),
    authorized_app_id: int | None = QueryParam(None, ge=1),
    owner_user_id: int | None = QueryParam(None, ge=1),
    date_from: datetime | None = QueryParam(None, description="Inclusive timezone-aware start; default 30 days before end"),
    date_to: datetime | None = QueryParam(None, description="Exclusive timezone-aware end; default now; maximum range 366 days"),
):
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return {"request_type": request_type, "status": status, "model": model, "provider": provider, "search": search,
                "authorized_app_id": authorized_app_id, "owner_user_id": owner_user_id,
                "date_from": date_from, "date_to": date_to}


@router.get("/requests", summary="List AI parent requests across existing history sources")
async def ai_requests(page: int = QueryParam(1, ge=1),
                      page_size: int = QueryParam(20, ge=1, le=100),
                      filters=Depends(history_filters), db=Depends(get_db)):
    from app.services.request_history import history_page

    return await history_page(db, filters, page, page_size)


@router.get("/requests/summary", summary="Aggregate all filtered AI parent requests")
async def ai_request_summary(filters=Depends(history_filters), db=Depends(get_db)):
    from app.services.request_history import history_summary

    return await history_summary(db, filters)


@router.get("/requests/{record_id}", summary="Inspect one AI parent request and recorded children")
async def ai_request_detail(record_id: str, response: Response,
                            m=Depends(member), db=Depends(get_db)):
    from app.services.request_history import history_detail

    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return await history_detail(db, record_id)


@router.get("/query-requests")
async def query_requests(response: Response, page: int = QueryParam(1, ge=1),
                         page_size: int = QueryParam(20, ge=1, le=100),
                         status: str | None = QueryParam(None, pattern="^(completed|failed|processing|cancelled|rejected)$"),
                         m=Depends(member), db=Depends(get_db)):
    from app.api.v1.query.router import request_page

    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return await request_page(db, [], page, page_size, status)


@router.get("/query-requests/{query_id}")
async def query_request_detail(query_id: int, response: Response, m=Depends(member), db=Depends(get_db)):
    from app.api.v1.query.router import request_detail

    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return await request_detail(db, query_id, [])


class Start(BaseModel):
    browser_binding_hash: str = Field(pattern="^[a-f0-9]{64}$")


class Exchange(BaseModel):
    ticket: str = Field(min_length=20, max_length=256)
    browser_binding: str = Field(min_length=32, max_length=128)


class Verify(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    code: str = Field(pattern="^[0-9]{6}$")


class RequestAccess(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    reason: str = Field(min_length=10, max_length=1000)


class Rule(BaseModel):
    resource_type: str
    resource_key: str = Field(max_length=512)
    action: str = "view"


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: str | None = Field(default=None, pattern="^(admin|operator|viewer)$")
    status: str | None = Field(default=None, pattern="^(pending|approved|revoked)$")
    permissions: list[Rule] | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    access_expires_at: datetime | None = None


class Invite(MemberUpdate):
    role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")
    status: str = Field(default="pending", pattern="^(pending|approved|revoked)$")
    permissions: list[Rule] = Field(default_factory=list, max_length=500)
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    name: str = Field(default="", max_length=80)


class CredentialInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(admin|app)$")
    secret: SecretStr = Field(min_length=10, max_length=8192)
    origin: str = ""


@router.post("/auth/start")
@limiter.limit("10/minute")
async def start(body: Start, request: Request, db=Depends(get_db)):
    check_origin(request)
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(503, "Backend Google OAuth is not configured")
    if settings.secret_key == "change-me-in-production":
        raise HTTPException(503, "Backend authentication secret is not configured")
    state = "dashboard." + secrets.token_urlsafe(32)
    verifier, nonce = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    db.add(
        OAuthTransaction(
            state_hash=digest(state),
            browser_binding_hash=body.browser_binding_hash,
            nonce=nonce,
            pkce_verifier=cipher().encrypt(verifier.encode()).decode(),
            expires_at=now() + timedelta(minutes=10),
        )
    )
    await db.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at < now()))
    await db.commit()
    callback = os.environ.get(
        "DASHBOARD_OAUTH_CALLBACK", "http://localhost:8000/api/v1/auth/callback"
    )
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("="),
        "code_challenge_method": "S256",
    }
    return {
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    }


async def oauth_callback(request, code, state, error, db):
    failure = dashboard_origin() + "/login?oauth=failed"
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    result = await db.execute(
        update(OAuthTransaction)
        .where(
            OAuthTransaction.state_hash == digest(state or ""),
            OAuthTransaction.consumed_at.is_(None),
            OAuthTransaction.expires_at > now(),
        )
        .values(consumed_at=now())
        .returning(OAuthTransaction.id)
    )
    identifier = result.scalar_one_or_none()
    await db.commit()
    if not identifier or error or not code:
        return RedirectResponse(failure, headers=headers)
    row = await db.get(OAuthTransaction, identifier)
    try:
        callback = os.environ.get(
            "DASHBOARD_OAUTH_CALLBACK", "http://localhost:8000/api/v1/auth/callback"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "code": code,
                    "redirect_uri": callback,
                    "grant_type": "authorization_code",
                    "code_verifier": cipher().decrypt(row.pkce_verifier.encode()).decode(),
                },
            )
            response.raise_for_status()
            tokens = response.json()
            token = tokens["id_token"]
            certs = await client.get("https://www.googleapis.com/oauth2/v3/certs")
            certs.raise_for_status()
        identity = jwt.decode(
            token,
            certs.json(),
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=["accounts.google.com", "https://accounts.google.com"],
            access_token=tokens.get("access_token"),
        )
        if (
            identity.get("nonce") != row.nonce
            or identity.get("email_verified") is not True
            or not identity.get("sub")
            or not identity.get("email")
        ):
            raise ValueError("Invalid identity")
        ticket = secrets.token_urlsafe(32)
        row.identity_json = json.dumps(
            {k: identity.get(k) for k in ("sub", "email", "name", "email_verified", "hd")}
        )
        row.completion_hash = digest(ticket)
        row.expires_at = now() + timedelta(seconds=60)
        await db.commit()
        return RedirectResponse(
            dashboard_origin() + "/auth/google/callback?ticket=" + ticket, headers=headers
        )
    except Exception as exc:
        logger.warning("Dashboard OAuth callback failed (%s)", type(exc).__name__)
        return RedirectResponse(failure, headers=headers)


@router.post("/auth/exchange")
@limiter.limit("15/minute")
async def exchange(body: Exchange, request: Request, db=Depends(get_db)):
    check_origin(request)
    result = await db.execute(
        update(OAuthTransaction)
        .where(
            OAuthTransaction.completion_hash == digest(body.ticket),
            OAuthTransaction.browser_binding_hash == digest(body.browser_binding),
            OAuthTransaction.expires_at > now(),
        )
        .values(completion_hash=None)
        .returning(OAuthTransaction.id)
    )
    identifier = result.scalar_one_or_none()
    await db.commit()
    if not identifier:
        raise HTTPException(401, "Expired or invalid login completion")
    row = await db.get(OAuthTransaction, identifier)
    identity = json.loads(row.identity_json)
    m = (
        await db.execute(select(Membership).where(Membership.email == identity["email"].lower()))
    ).scalar_one_or_none()
    if not m or m.status != "approved":
        raise HTTPException(403, "Ask an administrator to approve dashboard access")
    if not identity["email"].lower().endswith("@gmail.com") and not identity.get("hd"):
        return await email_challenge(db, identity["email"].lower(), "login", identity)
    return await establish(db, identity)


@router.post("/access-requests")
@limiter.limit("3/hour")
async def access_request(body: RequestAccess, request: Request, db=Depends(get_db)):
    check_origin(request)
    email = body.email.lower().strip()
    verification = await email_challenge(db, email, "request")
    row = (
        await db.execute(select(AccessRequest).where(AccessRequest.email == email))
    ).scalar_one_or_none()
    if not row:
        db.add(AccessRequest(email=email, name=body.name, reason=body.reason, created_at=now()))
    else:
        row.name, row.reason = body.name, body.reason
        row.email_verified = False
        if row.status != "approved":
            row.status = "pending"
    await db.commit()
    return verification


@router.post("/email-challenges/verify")
@limiter.limit("15/minute")
async def verify(body: Verify, request: Request, db=Depends(get_db)):
    check_origin(request)
    row = (
        await db.execute(
            select(EmailChallenge)
            .where(EmailChallenge.token_hash == digest(body.token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not row or row.consumed_at or aware(row.expires_at) <= now() or row.attempts >= 5:
        raise HTTPException(400, "Verification code expired or unavailable")
    row.attempts += 1
    expected = hmac.new(
        settings.secret_key.encode(), f"{body.token}:{body.code}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, row.challenge_hash):
        await db.commit()
        raise HTTPException(400, "Incorrect verification code")
    row.consumed_at = now()
    if row.purpose == "login":
        return await establish(db, json.loads(row.identity_json))
    entry = (
        await db.execute(select(AccessRequest).where(AccessRequest.email == row.email))
    ).scalar_one_or_none()
    if entry:
        entry.email_verified = True
    await db.commit()
    return {"verified": True}


@router.get("/me")
async def me(m=Depends(member), db=Depends(get_db)):
    return await dto(db, m)


@router.post("/logout")
async def logout(request: Request, m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    await db.execute(
        update(DashboardSession)
        .where(DashboardSession.token_hash == digest(token))
        .values(revoked_at=now())
    )
    await db.commit()
    return {"success": True}


def operations(request):
    return [
        (method.upper(), path, operation)
        for path, items in request.app.openapi()["paths"].items()
        if not path.startswith("/api/v1/dashboard")
        for method, operation in items.items()
        if method in {"get", "post", "patch", "put", "delete", "head", "options"}
        and operation_kind(method, path) is not None
    ]


def group(path):
    return path.split("/")[3] if path.startswith("/api/v1/") else "ops"


async def visible(db, m, method, path):
    return await member_operation_allowed(db, m, method, path)


@router.get("/catalog")
async def catalog(request: Request, m=Depends(member), db=Depends(get_db)):
    spec = request.app.openapi()
    paths = {}
    for method, path, op in operations(request):
        if await visible(db, m, method, path):
            paths.setdefault(path, {})[method.lower()] = op
    return {
        "spec": {"info": spec["info"], "paths": paths, "components": spec.get("components", {})},
        "live": True,
        "error": None,
    }


@router.get("/overview")
async def overview(m=Depends(member), db=Depends(get_db)):
    await require(db, m, "page", "overview")
    pending = (
        (
            await db.execute(
                select(func.count())
                .select_from(AccessRequest)
                .where(AccessRequest.status == "pending")
            )
        ).scalar()
        if m.role == "admin"
        else 0
    )
    team = (
        (await db.execute(select(func.count()).select_from(Membership))).scalar()
        if m.role == "admin"
        else None
    )
    keys = (
        (await db.execute(select(func.count()).select_from(ServiceCredential))).scalar()
        if m.role == "admin"
        else None
    )
    rows = []
    if await allowed(db, m, "page", "activity"):
        query = select(AuditLog).order_by(AuditLog.id.desc()).limit(5)
        if m.role != "admin":
            query = query.where(AuditLog.actor_membership_id == m.id)
        rows = [
            {
                "action": r.action,
                "target": r.target,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in (await db.execute(query)).scalars()
        ]
    return {
        "team": {"count": team, "pending": pending} if team is not None else None,
        "keys": keys,
        "activity": rows,
    }


@router.get("/activity")
async def activity(page: int = 1, m=Depends(member), db=Depends(get_db)):
    await require(db, m, "page", "activity")
    page = max(1, min(page, 100000))
    query = select(AuditLog, Membership.name).outerjoin(
        Membership, Membership.id == AuditLog.actor_membership_id
    )
    count = select(func.count()).select_from(AuditLog)
    if m.role != "admin":
        query = query.where(AuditLog.actor_membership_id == m.id)
        count = count.where(AuditLog.actor_membership_id == m.id)
    entries = (
        await db.execute(query.order_by(AuditLog.id.desc()).offset((page - 1) * 25).limit(25))
    ).all()
    return {
        "page": page,
        "count": (await db.execute(count)).scalar(),
        "rows": [
            {
                "id": r.id,
                "name": name or "System",
                "action": r.action,
                "target": r.target,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r, name in entries
        ],
    }


@router.get("/members")
async def members(request: Request, response: Response, m=Depends(member), db=Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    require_admin(m)
    records = (await db.execute(select(Membership).order_by(Membership.id.desc()))).scalars().all()
    requests = (
        (await db.execute(select(AccessRequest).order_by(AccessRequest.id.desc()))).scalars().all()
    )
    return {
        "users": [
            {**await dto(db, r), "master_key": None}
            for r in records
        ],
        "requests": [
            {
                "id": r.id,
                "email": r.email,
                "name": r.name,
                "reason": r.reason,
                "status": r.status,
                "email_verified": r.email_verified,
            }
            for r in requests
        ],
        "pages": PAGES,
        "components": COMPONENTS,
        "sections": sorted({group(path) for _, path, _ in operations(request)}),
        "grants": [
            f"{method} {path}"
            for method, path, _ in operations(request)
            if not path.startswith("/api/v1/auth/")
        ],
    }


async def save_rules(body, request, db, target):
    endpoint_keys = {
        f"{method} {path}"
        for method, path, _ in operations(request)
        if not path.startswith("/api/v1/auth/")
    }
    section_keys = {group(path) for _, path, _ in operations(request)}
    credentials = {str(r) for r in (await db.execute(select(ServiceCredential.id))).scalars()}
    valid = {
        "page": set(PAGES),
        "section": section_keys,
        "component": set(COMPONENTS),
        "endpoint": endpoint_keys,
        "credential": credentials,
    }
    unique = set()
    for r in body.permissions:
        if (
            r.resource_type not in valid
            or r.resource_key not in valid[r.resource_type]
            or r.action != ("execute" if r.resource_type in {"endpoint", "credential"} else "view")
        ):
            raise HTTPException(400, "Invalid permission grant")
        unique.add((r.resource_type, r.resource_key, r.action))
    await db.execute(delete(Permission).where(Permission.membership_id == target.id))
    for kind, key, action in unique:
        db.add(
            Permission(membership_id=target.id, resource_type=kind, resource_key=key, action=action)
        )


@router.post("/members")
async def invite(body: Invite, request: Request, m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    await lock_admin_change(db, m)
    if (
        await db.execute(select(Membership).where(Membership.email == body.email.lower()))
    ).scalar_one_or_none():
        raise HTTPException(409, "Membership already exists")
    target = Membership(
        email=body.email.lower(),
        name=body.name,
        role=body.role,
        status=body.status,
        approved_by=m.id,
        approved_at=now() if body.status == "approved" else None,
        created_at=now(),
    )
    db.add(target)
    await db.flush()
    await save_rules(body, request, db, target)
    entry = (
        await db.execute(select(AccessRequest).where(AccessRequest.email == target.email))
    ).scalar_one_or_none()
    if entry:
        if not entry.email_verified:
            raise HTTPException(400, "Verify the request email before approval")
        entry.status, entry.reviewed_by, entry.reviewed_at = body.status, m.id, now()
    target.access_expires_at = body.access_expires_at
    await audit(db, m, "INVITE_MEMBER", str(target.id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Membership already exists") from None
    return await dto(db, target)


@router.patch("/members/{identifier}")
async def change(
    identifier: int, body: MemberUpdate, request: Request, m=Depends(member), db=Depends(get_db)
):
    check_origin(request)
    require_admin(m)
    await lock_admin_change(db, m)
    if identifier == m.id:
        raise HTTPException(400, "Another administrator must change your access")
    target = await lock_target(db, identifier)
    if body.email is not None and body.email.lower() != target.email:
        raise HTTPException(400, "A member identity cannot be reassigned")
    role = body.role if body.role is not None else target.role
    status = body.status if body.status is not None else target.status
    expires = body.access_expires_at if "access_expires_at" in body.model_fields_set else target.access_expires_at
    await protect_administrator(db, target, role, status, expires)
    entry = (await db.execute(select(AccessRequest).where(
        AccessRequest.email == target.email
    ))).scalar_one_or_none()
    if entry and entry.status == "pending" and status == "approved" and not entry.email_verified:
        raise HTTPException(400, "Verify the request email before approval")
    if body.permissions is not None:
        await save_rules(body, request, db, target)
    if entry and entry.status == "pending":
        entry.status, entry.reviewed_by, entry.reviewed_at = status, m.id, now()
    target.role, target.status, target.access_expires_at = role, status, expires
    if body.name is not None:
        target.name = body.name
    target.approved_by, target.approved_at = m.id, now() if status == "approved" else None
    target.permission_version += 1
    await invalidate_member(db, target, revoke_keys=status != "approved" or bool(expires and aware(expires) <= now()))
    if target.api_project_id:
        from app.models.db_models import AuthorizedApp
        from app.services.access_policy import effective_app_grants
        project = await db.get(AuthorizedApp, target.api_project_id)
        if project and project.owner_user_id == target.user_id:
            project.allowed_endpoints_json = json.dumps(sorted(await effective_app_grants(db, target)))
    await audit(db, m, "UPDATE_MEMBER", str(target.id))
    await db.commit()
    return await dto(db, target)


@router.get("/members/{identifier}")
async def member_detail(identifier: int, response: Response, m=Depends(member), db=Depends(get_db)):
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    target = await db.get(Membership, identifier)
    if not target:
        raise HTTPException(404, "Member not found")
    return await dto(db, target)


@router.delete("/members/{identifier}")
async def delete_member(identifier: int, request: Request, response: Response, m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    await lock_admin_change(db, m)
    if identifier == m.id:
        raise HTTPException(400, "Another administrator must change your access")
    target = await lock_target(db, identifier)
    await protect_administrator(db, target, target.role, "revoked", target.access_expires_at)
    target.status = "revoked"
    target.permission_version += 1
    await invalidate_member(db, target, revoke_keys=True)
    await audit(db, m, "REVOKE_MEMBER", str(identifier))
    await db.commit()
    return {"success": True}


@router.post("/access-requests/{identifier}/reject")
async def reject(identifier: int, request: Request, m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    row = await db.get(AccessRequest, identifier)
    if not row:
        raise HTTPException(404, "Request not found")
    row.status, row.reviewed_by, row.reviewed_at = "rejected", m.id, now()
    await audit(db, m, "REJECT_REQUEST", str(identifier))
    await db.commit()
    return {"success": True}


@router.get("/credentials")
async def credentials(m=Depends(member), db=Depends(get_db)):
    require_admin(m)
    rows = (
        await db.execute(select(ServiceCredential).where(ServiceCredential.membership_id.is_(None))
                         .order_by(ServiceCredential.id.desc()))
    ).scalars().all()
    from app.models.db_models import AccessAPIKey, AuthorizedApp, User
    from app.services.api_key_service import customer_project_active
    candidates = (await db.execute(select(AuthorizedApp.id).join(
        AccessAPIKey, AccessAPIKey.authorized_app_id == AuthorizedApp.id
    ).join(User, AuthorizedApp.owner_user_id == User.id).where(
        AccessAPIKey.is_active.is_(True), AuthorizedApp.is_active.is_(True),
        AuthorizedApp.is_approved.is_(True), User.is_active.is_(True),
        User.api_access_enabled.is_(True),
    ).distinct())).scalars().all()
    configured_app = False
    for app_id in candidates:
        if await customer_project_active(db, app_id):
            configured_app = True
            break
    return {
        "credentials": [
            {
                "id": r.id,
                "label": r.label,
                "kind": r.kind,
                "app_origin": r.app_origin,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "configured": {
            "admin": bool(settings.cron_secret),
            "app": configured_app,
            "vault": bool(settings.encryption_key),
        },
    }


@router.get("/operation-credentials")
async def operation_credentials(m=Depends(member), db=Depends(get_db)):
    rows = (await db.execute(select(ServiceCredential).where(ServiceCredential.membership_id.is_(None)))).scalars().all()
    visible_rows = [r for r in rows if m.role == "admin" or (
        m.api_project_id is not None and r.kind == "app" and r.authorized_app_id == m.api_project_id
        and await allowed(db, m, "credential", str(r.id), "execute"))]
    return {
        "credentials": [
            {
                "id": r.id,
                "label": r.label,
                "kind": r.kind,
                "app_origin": r.app_origin,
                "created_at": r.created_at.isoformat(),
            }
            for r in visible_rows
        ]
    }


@router.post("/credentials")
async def save_credential(
    body: CredentialInput, request: Request, m=Depends(member), db=Depends(get_db)
):
    from app.services.app_access_service import normalize_origin

    check_origin(request)
    require_admin(m)
    try:
        origin = normalize_origin(body.origin) if body.kind == "app" and body.origin else None
    except ValueError:
        raise HTTPException(422, "Provide a valid app origin") from None
    from app.services.credential_access import app_credential_binding
    raw = body.secret.get_secret_value()
    key, app = await app_credential_binding(db, raw) if body.kind == "app" else (None, None)
    row = (
        await db.execute(select(ServiceCredential).where(ServiceCredential.label == body.label))
    ).scalar_one_or_none()
    if row and row.membership_id is not None:
        raise HTTPException(409, "Managed execution credentials cannot be replaced manually")
    if row and (row.kind != body.kind or (row.authorized_app_id and row.authorized_app_id != app.id)):
        raise HTTPException(409, "A saved credential cannot change kind or project")
    if row and row.kind == "app" and row.authorized_app_id is None:
        # Legacy assignments have no proven binding. Replacing clears their grants.
        await db.execute(delete(Permission).where(Permission.resource_type == "credential",
                                                  Permission.resource_key == str(row.id)))
    encrypted = cipher().encrypt(raw.encode()).decode()
    if row:
        row.encrypted_value, row.kind, row.app_origin = encrypted, body.kind, origin
        row.authorized_app_id, row.access_api_key_id = (app.id, key.id) if app else (None, None)
    else:
        db.add(
            ServiceCredential(
                label=body.label,
                kind=body.kind,
                encrypted_value=encrypted,
                app_origin=origin,
                created_by=m.id,
                created_at=now(),
                authorized_app_id=app.id if app else None,
                access_api_key_id=key.id if key else None,
            )
        )
    await audit(db, m, "SAVE_CREDENTIAL", "vault")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Credential label or key already exists") from None
    return {"success": True}


@router.delete("/credentials/{identifier}")
async def delete_credential(
    identifier: int, request: Request, m=Depends(member), db=Depends(get_db)
):
    check_origin(request)
    require_admin(m)
    row = await db.get(ServiceCredential, identifier)
    if row and row.membership_id is not None:
        raise HTTPException(409, "Revoke the member to remove managed execution access")
    await db.execute(
        delete(Permission).where(
            Permission.resource_type == "credential", Permission.resource_key == str(identifier)
        )
    )
    await db.execute(delete(ServiceCredential).where(ServiceCredential.id == identifier))
    await audit(db, m, "DELETE_CREDENTIAL", str(identifier))
    await db.commit()
    return {"success": True}


@router.post("/operations/{identifier}")
async def execute(identifier: str, request: Request, m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    from app.services.customer_keys import execution_credential, locked_member
    m = await locked_member(db, m.id)
    selected = next(
        (
            (method, path, op)
            for method, path, op in operations(request)
            if base64.urlsafe_b64encode(f"{method.lower()}:{path}".encode()).decode().rstrip("=")
            == identifier
        ),
        None,
    )
    if not selected:
        raise HTTPException(404, "Endpoint not found")
    method, path, op = selected
    kind = operation_kind(method, path)
    if not await member_operation_allowed(db, m, method, path):
        raise HTTPException(403, "Operation execution denied")
    if int(request.headers.get("content-length", "0")) > 22 * 1024 * 1024:
        raise HTTPException(413, "Request too large")
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > 22 * 1024 * 1024:
            raise HTTPException(413, "Request too large")
    request._body = bytes(payload)
    form = await request.form()
    if (method not in {"GET", "HEAD"} or group(path) == "cron") and form.get("confirmed") != "on":
        raise HTTPException(400, "Confirm this operation before execution")
    headers = {}
    try:
        credential = int(form.get("credential") or 0)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid credential selection") from None
    if kind in {"admin", "app"}:
        if credential:
            row = await db.get(ServiceCredential, credential)
            if not row or row.kind != kind:
                raise HTTPException(400, "Select a matching credential")
            await require(db, m, "credential", str(credential), "execute")
            try:
                raw = cipher().decrypt(row.encrypted_value.encode()).decode()
            except Exception:
                raise HTTPException(503, "Saved credential unavailable") from None
            if kind == "app":
                from app.services.credential_access import checked_credential
                await checked_credential(db, row, raw, m)
            headers = (
                {"X-Cron-Secret": raw}
                if kind == "admin"
                else {"X-API-Key": raw}
            )
        elif kind == "admin" and m.role == "admin" and settings.cron_secret:
            headers = {"X-Cron-Secret": settings.cron_secret}
        elif kind == "app":
            _, raw = await execution_credential(db, m)
            headers = {"X-API-Key": raw}
        else:
            raise HTTPException(503, "Admin authorization is not configured")
    params = {}
    for p in op.get("parameters", []):
        if p["in"] not in {"path", "query"}:
            continue
        value = form.get(f"param:{p['in']}:{p['name']}")
        if p.get("required") and not value:
            raise HTTPException(400, "Required parameter is missing")
        if value:
            value = str(value)
            if p["in"] == "path":
                if re.search(r"[/\\%?#]", value) or value in {".", ".."}:
                    raise HTTPException(400, "Invalid path parameter")
                path = path.replace("{" + p["name"] + "}", quote(value, safe=""))
            else:
                params[p["name"]] = value
    kwargs = {"headers": headers, "params": params}
    if "json" in str(form.get("contentType", "")):
        try:
            kwargs["json"] = json.loads(str(form.get("body") or "{}"))
        except ValueError:
            raise HTTPException(400, "Enter valid JSON") from None
    else:
        files, data = {}, {}
        for name, value in form.multi_items():
            if not name.startswith("body:"):
                continue
            key = name[5:]
            if hasattr(value, "read"):
                content = await value.read(20 * 1024 * 1024 + 1)
                if len(content) > 20 * 1024 * 1024:
                    raise HTTPException(413, "File too large")
                files[key] = (value.filename, content, value.content_type)
            else:
                data[key] = str(value)
        if files:
            kwargs["files"] = files
        if data:
            kwargs["data"] = data
    if method == "POST" and path == "/api/v1/keys/":
        cipher()
    # The upstream uses a separate DB session and must see the managed key first.
    await db.commit()
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            async with client.stream(
                method,
                os.environ.get("DASHBOARD_INTERNAL_URL", "http://127.0.0.1:8000").rstrip("/")
                + path,
                **kwargs,
            ) as upstream:
                chunks = bytearray()
                async for chunk in upstream.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2 * 1024 * 1024:
                        raise HTTPException(502, "Response exceeds preview limit")
                status = upstream.status_code
                content_type = upstream.headers.get("content-type", "")
        if "json" in content_type:
            result = json.loads(chunks)
            if (
                path == "/api/v1/keys/"
                and method == "POST"
                and status < 300
                and isinstance(result, dict)
                and result.get("raw_key")
            ):
                label = f"App key {result.get('id', secrets.token_hex(4))}"
                db.add(
                    ServiceCredential(
                        label=label,
                        kind="app",
                        encrypted_value=cipher().encrypt(result["raw_key"].encode()).decode(),
                        app_origin=result.get("allowed_domain"),
                        authorized_app_id=result["authorized_app_id"],
                        access_api_key_id=result["id"],
                        created_by=m.id,
                        created_at=now(),
                    )
                )
            result = redact(result, tuple(headers.values()))
        else:
            result = {"content_type": content_type, "size_bytes": len(chunks)}
        await audit(
            db, m, method, selected[1], status, round((time.monotonic() - started) * 1000, 1)
        )
        await db.commit()
        return {
            "status": status,
            "elapsed": round((time.monotonic() - started) * 1000),
            "result": result,
            "error": "Gateway rejected this operation" if status >= 400 else None,
        }
    except HTTPException as exc:
        await audit(db, m, method, selected[1], exc.status_code, round((time.monotonic()-started)*1000, 1))
        await db.commit()
        raise
    except Exception:
        await audit(db, m, method, selected[1], 502, round((time.monotonic() - started) * 1000, 1))
        await db.commit()
        raise HTTPException(502, "Gateway operation could not be completed") from None


@router.get("/analytics")
async def analytics(days: int = 7, m=Depends(member), db=Depends(get_db)):
    from app.services.analytics import dashboard_analytics

    await require(db, m, "page", "overview")
    return await dashboard_analytics(db, m, days)
