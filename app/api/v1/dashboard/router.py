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
from datetime import timedelta
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
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Query as QueryParam
from fastapi.responses import RedirectResponse, Response
from jose import jwt
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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
    role: str = Field(pattern="^(admin|operator|viewer)$")
    status: str = Field(pattern="^(pending|approved|revoked)$")
    permissions: list[Rule] = Field(default_factory=list, max_length=500)


class Invite(MemberUpdate):
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    name: str = Field(default="", max_length=80)


class CredentialInput(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(admin|app)$")
    secret: str = Field(min_length=10, max_length=8192)
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
    ]


def group(path):
    return path.split("/")[3] if path.startswith("/api/v1/") else "ops"


async def visible(db, m, method, path):
    return (
        await allowed(db, m, "page", "endpoints")
        and await allowed(db, m, "section", group(path))
        and await allowed(db, m, "endpoint", f"{method} {path}", "execute")
    )


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
            {**await dto(db, r), "master_key": cipher().decrypt(r.master_key_encrypted.encode()).decode()
             if r.master_key_encrypted else None}
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
    if target.status == "approved":
        from app.services.customer_keys import ensure_master_key
        ensure_master_key(target)
    await audit(db, m, "INVITE_MEMBER", str(target.id))
    await db.commit()
    return await dto(db, target)


@router.patch("/members/{identifier}")
async def change(
    identifier: int, body: MemberUpdate, request: Request, m=Depends(member), db=Depends(get_db)
):
    check_origin(request)
    require_admin(m)
    if identifier == m.id:
        raise HTTPException(400, "Another administrator must change your access")
    target = await db.get(Membership, identifier)
    if not target:
        raise HTTPException(404, "Member not found")
    entry = (await db.execute(select(AccessRequest).where(
        AccessRequest.email == target.email
    ))).scalar_one_or_none()
    if entry and entry.status == "pending" and body.status == "approved" and not entry.email_verified:
        raise HTTPException(400, "Verify the request email before approval")
    await save_rules(body, request, db, target)
    if entry and entry.status == "pending":
        entry.status, entry.reviewed_by, entry.reviewed_at = body.status, m.id, now()
    target.role, target.status = body.role, body.status
    target.approved_by, target.approved_at = m.id, now() if body.status == "approved" else None
    if target.status == "approved":
        from app.services.customer_keys import ensure_master_key
        ensure_master_key(target)
    target.permission_version += 1
    await db.execute(
        update(DashboardSession)
        .where(DashboardSession.membership_id == target.id)
        .values(revoked_at=now())
    )
    await audit(db, m, "UPDATE_MEMBER", str(target.id))
    await db.commit()
    return await dto(db, target)


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
        await db.execute(select(ServiceCredential).order_by(ServiceCredential.id.desc()))
    ).scalars()
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
            "app": False,
            "vault": bool(settings.encryption_key),
        },
    }


@router.get("/operation-credentials")
async def operation_credentials(m=Depends(member), db=Depends(get_db)):
    await require(db, m, "page", "endpoints")
    rows = (await db.execute(select(ServiceCredential))).scalars().all()
    visible_rows = [r for r in rows if await allowed(db, m, "credential", str(r.id), "execute")]
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
    origin = normalize_origin(body.origin) if body.kind == "app" else None
    row = (
        await db.execute(select(ServiceCredential).where(ServiceCredential.label == body.label))
    ).scalar_one_or_none()
    encrypted = cipher().encrypt(body.secret.encode()).decode()
    if row:
        row.encrypted_value, row.kind, row.app_origin = encrypted, body.kind, origin
    else:
        db.add(
            ServiceCredential(
                label=body.label,
                kind=body.kind,
                encrypted_value=encrypted,
                app_origin=origin,
                created_by=m.id,
                created_at=now(),
            )
        )
    await audit(db, m, "SAVE_CREDENTIAL", "vault")
    await db.commit()
    return {"success": True}


@router.delete("/credentials/{identifier}")
async def delete_credential(
    identifier: int, request: Request, m=Depends(member), db=Depends(get_db)
):
    check_origin(request)
    require_admin(m)
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
    await require(db, m, "page", "endpoints")
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
    kind = (
        "oauth"
        if path.startswith("/api/v1/auth/")
        else "admin"
        if re.match(r"^/api/v1/(apps|keys|cron)(/|$)", path)
        else "app"
        if re.match(r"^/api/v1/(audio|ingest|query|image|video|media|facebook)(/|$)", path)
        else "public"
    )
    if kind == "oauth" or m.role == "viewer" or (kind == "admin" and m.role != "admin"):
        raise HTTPException(403, "Operation execution denied")
    await require(db, m, "section", group(path))
    await require(db, m, "endpoint", f"{method} {path}", "execute")
    await require(db, m, "component", "endpoint.execute")
    component = {
        "POST": "endpoint.create",
        "PATCH": "endpoint.update",
        "PUT": "endpoint.update",
        "DELETE": "endpoint.delete",
    }.get(method)
    if component:
        await require(db, m, "component", component)
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
    credential = int(form.get("credential") or 0)
    if kind in {"admin", "app"}:
        if credential:
            row = await db.get(ServiceCredential, credential)
            if not row or row.kind != kind:
                raise HTTPException(400, "Select a matching credential")
            await require(db, m, "credential", str(credential), "execute")
            raw = cipher().decrypt(row.encrypted_value.encode()).decode()
            headers = (
                {"X-Cron-Secret": raw}
                if kind == "admin"
                else {"X-API-Key": raw, "X-App-Origin": row.app_origin}
            )
        elif kind == "admin" and m.role == "admin" and settings.cron_secret:
            headers = {"X-Cron-Secret": settings.cron_secret}
        else:
            raise HTTPException(400, "Select an assigned service credential")
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
                        created_by=m.id,
                        created_at=now(),
                    )
                )
            result = redact(result)
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
