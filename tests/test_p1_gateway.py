import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.api.v1.audio.schemas import DynamicTTSRequest
from app.models.db_models import AuthorizedApp, FacebookPageToken, TTSJob, User
from app.services.facebook_management import manage_post
from app.services.tts.submission import submission_identity
from fastapi import HTTPException
from starlette.requests import Request


def req(key="fixture-key-123456", external="frontend-fixture"):
    return Request({"type": "http", "headers": [(b"idempotency-key", key.encode()), (b"x-external-user-id", external.encode())]})


@pytest.mark.asyncio
async def test_tts_submission_is_app_and_frontend_scope_bound(unit_db):
    payload = DynamicTTSRequest(text="Fixture", model="fixture")
    a = await submission_identity(unit_db, req(), payload, 1)
    b = await submission_identity(unit_db, req(external="other"), payload, 1)
    c = await submission_identity(unit_db, req(), payload, 2)
    assert len({a[0], b[0], c[0]}) == 3


@pytest.mark.asyncio
async def test_tts_replay_returns_same_job_and_changed_payload_conflicts(unit_db):
    # SQLite foreign keys are disabled for this pure submission identity fixture.
    payload = DynamicTTSRequest(text="Fixture", model="fixture")
    identifier, fingerprint, _ = await submission_identity(unit_db, req(), payload, None)
    job = TTSJob(job_id=identifier, text="Fixture", status="processing",
                 generation_json=json.dumps({"submission_fingerprint": fingerprint}))
    unit_db.add(job)
    await unit_db.commit()
    assert (await submission_identity(unit_db, req(), payload, None))[2].job_id == identifier
    with pytest.raises(HTTPException) as caught:
        await submission_identity(unit_db, req(), DynamicTTSRequest(text="Changed", model="fixture"), None)
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_invalid_submission_identity_cannot_create_job(unit_db):
    with pytest.raises(HTTPException) as caught:
        await submission_identity(unit_db, req(key="short"), DynamicTTSRequest(text="Fixture"), 1)
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_managing_foreign_page_never_contacts_meta(unit_db, monkeypatch):
    factory = AsyncMock()
    monkeypatch.setattr("app.services.facebook_management.httpx.AsyncClient", factory)
    with pytest.raises(HTTPException) as caught:
        await manage_post(unit_db, 1, "123", "123_456", remove=True)
    assert caught.value.status_code == 404
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_owner,published,success,expected", [
    ("999", False, True, 404), ("123", True, True, 409),
    ("123", False, False, 502), ("123", False, True, 200),
])
async def test_remote_cancel_verifies_page_and_scheduled_state(unit_db, monkeypatch, remote_owner, published, success, expected):
    owner = User(google_id="fixture", email="fixture@test", is_active=True)
    unit_db.add(owner)
    await unit_db.flush()
    app = AuthorizedApp(name="fixture", owner_user_id=owner.id, allowed_origin="https://fixture.test",
                        is_active=True, is_approved=True, allowed_endpoints_json="[]")
    unit_db.add(app)
    await unit_db.flush()
    unit_db.add(FacebookPageToken(authorized_app_id=app.id, user_id=owner.id, page_id="123",
                                 encrypted_token="fixture", is_active=True))
    await unit_db.commit()
    monkeypatch.setattr("app.services.facebook_management.decrypt_token", lambda _: "fixture")
    calls = []
    def remote(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"from": {"id": remote_owner}, "is_published": published, "scheduled_publish_time": int(time.time()) + 3600})
        return httpx.Response(200, json={"success": success})
    client = httpx.AsyncClient
    monkeypatch.setattr("app.services.facebook_management.httpx.AsyncClient",
                        lambda **kwargs: client(transport=httpx.MockTransport(remote), **kwargs))
    if expected == 200:
        assert (await manage_post(unit_db, app.id, "123", "123_456", remove=True))["status"] == "success"
    else:
        with pytest.raises(HTTPException) as caught:
            await manage_post(unit_db, app.id, "123", "123_456", remove=True)
        assert caught.value.status_code == expected
    if expected in {404, 409}:
        assert calls == ["GET"]
