"""app/services/api_key_service.py — Management and verification for AccessAPIKey."""

import datetime
import hashlib
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.app_access_service import normalize_origin

logger = logging.getLogger(__name__)

KEY_PREFIX_LENGTH = 12  # e.g., "gw_live_a1b2c3"


def hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key string."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def generate_api_key(
    db: AsyncSession,
    client_name: str,
    authorized_app_id: int,
) -> tuple[str, AccessAPIKey]:
    """
    Generate a new API key for a third-party client application.
    Returns (raw_secret_key, db_record). The raw_secret_key should be displayed ONCE to the user.
    """
    app = await db.get(AuthorizedApp, authorized_app_id)
    if app is None:
        raise ValueError("Authorized app not found")
    owner = await db.get(User, app.owner_user_id)
    if not app.is_active or not app.is_approved or not owner or not owner.is_active or not owner.api_access_enabled:
        raise ValueError("App and its gateway owner must have API permission before issuing a key")
    raw_token = secrets.token_urlsafe(32)
    raw_key = f"gw_live_{raw_token}"
    key_prefix = raw_key[:KEY_PREFIX_LENGTH]
    hashed = hash_key(raw_key)

    api_key_record = AccessAPIKey(
        client_name=client_name.strip(),
        authorized_app_id=app.id,
        allowed_domain=app.allowed_origin,
        key_prefix=key_prefix,
        hashed_key=hashed,
        is_active=True,
    )
    db.add(api_key_record)
    await db.commit()
    await db.refresh(api_key_record)

    logger.info("Generated new AccessAPIKey for client='%s' (prefix='%s')", client_name, key_prefix)
    return raw_key, api_key_record


async def verify_api_key(
    db: AsyncSession,
    raw_key: str,
    origin_domain: str | None = None,
) -> AccessAPIKey | None:
    """
    Verify a raw API key against the database.
    If valid and active, updates last_used_at timestamp and returns the AccessAPIKey record.
    """
    if not raw_key or not raw_key.strip() or not origin_domain:
        return None

    try:
        origin = normalize_origin(origin_domain)
    except ValueError:
        return None

    hashed = hash_key(raw_key.strip())
    stmt = select(AccessAPIKey).join(
        AuthorizedApp, AccessAPIKey.authorized_app_id == AuthorizedApp.id
    ).join(User, AuthorizedApp.owner_user_id == User.id).where(
        AccessAPIKey.hashed_key == hashed,
        AccessAPIKey.is_active.is_(True),
        AuthorizedApp.allowed_origin == origin,
        AuthorizedApp.is_active.is_(True),
        AuthorizedApp.is_approved.is_(True),
        User.is_active.is_(True),
        User.api_access_enabled.is_(True),
    )
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if not record:
        logger.warning("API key verification failed: Key not found or inactive")
        return None

    # Update last_used_at
    record.last_used_at = datetime.datetime.now(datetime.UTC)
    await db.commit()

    return record


async def revoke_api_key(db: AsyncSession, key_id: int) -> bool:
    """Deactivate/revoke an API key by ID."""
    stmt = select(AccessAPIKey).where(AccessAPIKey.id == key_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        return False

    record.is_active = False
    await db.commit()
    logger.info("Revoked AccessAPIKey id=%d (client='%s')", key_id, record.client_name)
    return True


async def list_api_keys(db: AsyncSession) -> list[AccessAPIKey]:
    """Retrieve all AccessAPIKey records."""
    stmt = select(AccessAPIKey).order_by(AccessAPIKey.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
