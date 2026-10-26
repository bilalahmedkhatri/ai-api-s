"""Transactional customer provisioning and server-only execution credentials."""

import json
import secrets

from fastapi import HTTPException
from sqlalchemy import select

from app.models.dashboard import Membership, ServiceCredential
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.access_policy import effective_app_grants
from app.services.api_key_service import generate_api_key, hash_key
from app.services.dashboard import aware, cipher, now


async def locked_member(db, identifier):
    row = (
        await db.execute(
            select(Membership)
            .where(Membership.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        not row
        or row.status != "approved"
        or (row.access_expires_at and aware(row.access_expires_at) <= now())
    ):
        raise HTTPException(403, "Dashboard access is not approved")
    return row


async def ensure_project(db, m):
    """Caller holds the membership lock; never enable an existing disabled owner."""
    owner = (
        await db.execute(
            select(User)
            .where(User.id == m.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not owner or not owner.is_active or not owner.api_access_enabled:
        raise HTTPException(403, "Account API access is disabled")
    grants = json.dumps(sorted(await effective_app_grants(db, m)))
    if m.api_project_id:
        project = await db.get(AuthorizedApp, m.api_project_id)
        if (
            not project
            or project.owner_user_id != owner.id
            or not project.is_active
            or not project.is_approved
        ):
            raise HTTPException(403, "API project is disabled")
        project.allowed_endpoints_json = grants
    else:
        project = AuthorizedApp(
            owner_user_id=owner.id,
            name="Customer API",
            allowed_origin="",
            allowed_endpoints_json=grants,
            is_active=True,
            is_approved=True,
        )
        db.add(project)
        await db.flush()
        m.api_project_id = project.id
    m.api_keys_unlocked = True  # Compatibility metadata; no longer an authorization gate.
    return project


async def execution_credential(db, m):
    """One encrypted managed key per member; never returned to a browser."""
    m = await locked_member(db, m.id)
    project = await ensure_project(db, m)
    vault = cipher()
    row = (
        await db.execute(select(ServiceCredential).where(ServiceCredential.membership_id == m.id))
    ).scalar_one_or_none()
    if row:
        key = await db.get(AccessAPIKey, row.access_api_key_id) if row.access_api_key_id else None
        if row.authorized_app_id != project.id:
            raise HTTPException(403, "Execution credential ownership mismatch")
        if key and key.is_active and key.is_internal and key.authorized_app_id == project.id:
            try:
                raw = vault.decrypt(row.encrypted_value.encode()).decode()
            except Exception:
                raise HTTPException(503, "Execution credential unavailable") from None
            if hash_key(raw) != key.hashed_key:
                raise HTTPException(503, "Execution credential unavailable")
            return row, raw
    raw, key = await generate_api_key(
        db, "Dashboard execution", project.id, commit=False, is_internal=True
    )
    if not row:
        row = ServiceCredential(
            label=f"Managed member {m.id} {secrets.token_hex(4)}",
            kind="app",
            membership_id=m.id,
            authorized_app_id=project.id,
            created_by=m.id,
            created_at=now(),
        )
        db.add(row)
    row.access_api_key_id = key.id
    row.encrypted_value = vault.encrypt(raw.encode()).decode()
    row.app_origin = project.allowed_origin
    await db.flush()
    return row, raw
