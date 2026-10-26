"""Dashboard regressions use isolated DB and mocked Google/mail/HTTP providers."""

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from app.core.config import settings
from app.models.dashboard import (
    AccessRequest,
    DashboardSession,
    EmailChallenge,
    Membership,
    OAuthTransaction,
    Permission,
)
from app.models.db_models import User
from app.services.dashboard import digest, establish, now
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select

ORIGIN = {"Origin": "http://localhost:3002"}


@pytest.fixture
def dashboard(unit_db, monkeypatch):
    monkeypatch.setenv("DASHBOARD_ORIGIN", "http://localhost:3002")
    monkeypatch.setattr(settings, "secret_key", "isolated-dashboard-secret")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "google_client_id", "isolated-client")
    monkeypatch.setattr(settings, "google_client_secret", "isolated-google-secret")
    owner = User(
        google_id="existing-google",
        email="admin@gmail.com",
        is_active=True,
        api_access_enabled=False,
    )
    unit_db.add(owner)
    unit_db.session.flush()
    m = Membership(
        email=owner.email,
        user_id=owner.id,
        name="Admin",
        role="admin",
        status="approved",
        created_at=now(),
    )
    unit_db.add(m)
    unit_db.session.flush()
    unit_db.add(
        DashboardSession(
            token_hash=digest("isolated-token"),
            membership_id=m.id,
            created_at=now(),
            expires_at=now() + timedelta(hours=1),
        )
    )
    unit_db.session.commit()
    return unit_db, m, {**ORIGIN, "Authorization": "Bearer isolated-token"}


@pytest.mark.asyncio
async def test_dashboard_approval_independent_of_app_access(dashboard):
    db, m, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/dashboard/me", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/dashboard/members", headers={})).status_code == 401
        m.status = "revoked"
        await db.commit()
        assert (await client.get("/api/v1/dashboard/me", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_oauth_start_pkce_and_fixed_backend_callback(dashboard):
    db, m, headers = dashboard
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post(
            "/api/v1/dashboard/auth/start",
            headers=ORIGIN,
            json={"browser_binding_hash": digest("browser-secret")},
        )
        assert result.status_code == 200
        from urllib.parse import parse_qs, urlparse

        url = result.json()["authorization_url"]
        params = parse_qs(urlparse(url).query)
        assert params["code_challenge_method"] == ["S256"]
        assert params["prompt"] == ["select_account"]
        assert params["redirect_uri"] == ["http://localhost:8000/api/v1/auth/callback"]
        assert "isolated-google-secret" not in url
        assert (
            await client.post(
                "/api/v1/dashboard/auth/start",
                headers={"Origin": "https://attacker.test"},
                json={"browser_binding_hash": digest("browser-secret")},
            )
        ).status_code == 403


@pytest.mark.asyncio
async def test_exchange_bound_to_browser_and_consumed_once(dashboard):
    db, m, headers = dashboard
    db.add(
        OAuthTransaction(
            state_hash=digest("state"),
            browser_binding_hash=digest("b" * 32),
            nonce="n",
            pkce_verifier="encrypted",
            expires_at=now() + timedelta(seconds=60),
            completion_hash=digest("t" * 32),
            identity_json=json.dumps(
                {"sub": "existing-google", "email": "admin@gmail.com", "email_verified": True}
            ),
        )
    )
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = "/api/v1/dashboard/auth/exchange"
        assert (
            await client.post(
                path, headers=ORIGIN, json={"ticket": "t" * 32, "browser_binding": "a" * 32}
            )
        ).status_code == 401
        result = await client.post(
            path, headers=ORIGIN, json={"ticket": "t" * 32, "browser_binding": "b" * 32}
        )
        assert result.status_code == 200
        assert result.json()["user"]["id"] == m.id
        assert (
            await client.post(
                path, headers=ORIGIN, json={"ticket": "t" * 32, "browser_binding": "b" * 32}
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_unlisted_identity_does_not_register_gateway_user(dashboard):
    db, m, headers = dashboard
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await establish(db, {"sub": "unlisted", "email": "unlisted@gmail.com"})
    assert (
        await db.execute(select(User).where(User.google_id == "unlisted"))
    ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_grants_sections_components_and_direct_operation_denial(dashboard):
    db, m, headers = dashboard
    m.role = "operator"
    db.add(
        Permission(
            membership_id=m.id, resource_type="page", resource_key="endpoints", action="view"
        )
    )
    db.add(
        Permission(
            membership_id=m.id,
            resource_type="endpoint",
            resource_key="GET /api/v1/audio/models",
            action="execute",
        )
    )
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get("/api/v1/dashboard/catalog", headers=headers)).json()
        assert "/api/v1/audio/models" in data["spec"]["paths"]
        assert (await client.get("/api/v1/dashboard/members", headers=headers)).status_code == 403
        import base64

        identifier = base64.urlsafe_b64encode(b"post:/api/v1/audio/tts").decode().rstrip("=")
        assert (
            await client.post("/api/v1/dashboard/operations/" + identifier, headers=headers)
        ).status_code == 403


@pytest.mark.asyncio
async def test_email_code_attempts_expiry_and_replay(dashboard):
    db, m, headers = dashboard
    token = "v" * 32
    hashed = hmac.new(
        settings.secret_key.encode(), f"{token}:123456".encode(), hashlib.sha256
    ).hexdigest()
    db.add(
        AccessRequest(
            email="request@example.test", name="Request", reason="Access needed", created_at=now()
        )
    )
    db.add(
        EmailChallenge(
            token_hash=digest(token),
            email="request@example.test",
            challenge_hash=hashed,
            purpose="request",
            expires_at=now() + timedelta(minutes=10),
        )
    )
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = "/api/v1/dashboard/email-challenges/verify"
        assert (
            await client.post(path, headers=ORIGIN, json={"token": token, "code": "000000"})
        ).status_code == 400
        assert (
            await client.post(path, headers=ORIGIN, json={"token": token, "code": "123456"})
        ).status_code == 200
        assert (
            await client.post(path, headers=ORIGIN, json={"token": token, "code": "123456"})
        ).status_code == 400
        assert (await db.execute(select(AccessRequest))).scalar_one().email_verified


@pytest.mark.asyncio
async def test_access_request_requires_delivered_code_before_approval(dashboard, monkeypatch):
    db, m, headers = dashboard
    import re

    from app.services import dashboard as service

    captured = []

    class Mail:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            pass

        def send_message(self, message):
            captured.append(message.get_content())

    monkeypatch.setenv("SMTP_HOST", "mock-smtp")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.test")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.setattr(service.smtplib, "SMTP", Mail)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        submitted = await client.post(
            "/api/v1/dashboard/access-requests",
            headers=ORIGIN,
            json={
                "name": "New member",
                "email": "new@example.test",
                "reason": "Need access to audio management",
            },
        )
        assert submitted.status_code == 200
        row = (
            await db.execute(select(AccessRequest).where(AccessRequest.email == "new@example.test"))
        ).scalar_one()
        assert not row.email_verified and row.status == "pending"
        result = await client.post(
            "/api/v1/dashboard/email-challenges/verify",
            headers=ORIGIN,
            json={
                "token": submitted.json()["verification_token"],
                "code": re.search(r"\b[0-9]{6}\b", captured[0]).group(),
            },
        )
        assert result.status_code == 200 and result.json()["verified"]
        assert "session_token" not in result.json()
        assert row.email_verified and row.status == "pending"
        assert (
            await db.execute(select(User).where(User.email == "new@example.test"))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "returned_access_token,success",
    [("google-access", True), ("wrong-access", False), (None, False)],
)
async def test_google_callback_validates_access_token_hash(
    dashboard, monkeypatch, returned_access_token, success
):
    from urllib.parse import parse_qs, urlparse

    import httpx
    from app.api.v1.dashboard import router as module
    from app.services.dashboard import cipher
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi import Request
    from jose import jwk, jwt
    from jose.utils import calculate_at_hash

    db, member, headers = dashboard
    state = "dashboard.isolated-state"
    transaction = OAuthTransaction(
        state_hash=digest(state),
        browser_binding_hash=digest("b" * 32),
        nonce="isolated-nonce",
        pkce_verifier=cipher().encrypt(b"isolated-pkce-verifier").decode(),
        expires_at=now() + timedelta(minutes=10),
    )
    db.add(transaction)
    await db.commit()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    token = jwt.encode(
        {
            "aud": settings.google_client_id,
            "iss": "https://accounts.google.com",
            "exp": int((now() + timedelta(minutes=5)).timestamp()),
            "nonce": transaction.nonce,
            "sub": "existing-google",
            "email": "admin@gmail.com",
            "email_verified": True,
            "at_hash": calculate_at_hash("google-access", hashlib.sha256),
        },
        private,
        algorithm="RS256",
    )

    class Google:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, data):
            assert url == "https://oauth2.googleapis.com/token"
            assert data["code_verifier"] == "isolated-pkce-verifier"
            body = {"id_token": token}
            if returned_access_token is not None:
                body["access_token"] = returned_access_token
            return httpx.Response(200, json=body, request=httpx.Request("POST", url))

        async def get(self, url):
            return httpx.Response(
                200,
                json={"keys": [jwk.construct(public, algorithm="RS256").to_dict()]},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", Google)
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/auth/callback", "headers": []}
    )
    response = await module.oauth_callback(request, "google-code", state, None, db)
    location = response.headers["location"]
    if success:
        assert urlparse(location).path == "/auth/google/callback"
        ticket = parse_qs(urlparse(location).query)["ticket"][0]
        assert transaction.completion_hash == digest(ticket)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            completed = await client.post(
                "/api/v1/dashboard/auth/exchange",
                headers=ORIGIN,
                json={"ticket": ticket, "browser_binding": "b" * 32},
            )
        assert completed.status_code == 200
        assert completed.json()["user"]["id"] == member.id
        assert completed.json()["session_token"]
    else:
        assert location == "http://localhost:3002/login?oauth=failed"
        assert transaction.identity_json is None and transaction.completion_hash is None
