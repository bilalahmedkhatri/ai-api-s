"""Validate imported keys against persisted app ownership, not labels or origins."""

from fastapi import HTTPException
from sqlalchemy import select

from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.api_key_service import customer_project_active, hash_key


async def app_credential_binding(db, raw):
    key = (
        await db.execute(
            select(AccessAPIKey)
            .where(AccessAPIKey.hashed_key == hash_key(raw), AccessAPIKey.is_active.is_(True))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    app = await db.get(AuthorizedApp, key.authorized_app_id) if key else None
    owner = await db.get(User, app.owner_user_id) if app else None
    if (
        not key
        or not app
        or not owner
        or not app.is_active
        or not app.is_approved
        or not owner.is_active
        or not owner.api_access_enabled
        or not await customer_project_active(db, app.id)
    ):
        raise HTTPException(400, "Provide an active app API key")
    return key, app


async def checked_credential(db, row, raw, member):
    key, app = await app_credential_binding(db, raw)
    if row.access_api_key_id != key.id or row.authorized_app_id != app.id:
        raise HTTPException(403, "Credential ownership must be verified before use")
    if member.role != "admin" and (
        app.owner_user_id != member.user_id or app.id != member.api_project_id
    ):
        raise HTTPException(403, "Only your own project credential may be used")
    return key, app
