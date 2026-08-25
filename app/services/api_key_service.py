"""app/services/api_key_service.py — Management and verification for AccessAPIKey."""

import datetime
import hashlib
import logging
import secrets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db_models import AccessAPIKey

logger = logging.getLogger(__name__)

KEY_PREFIX_LENGTH = 12  # e.g., "gw_live_a1b2c3"


def hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key string."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def generate_api_key(
    db: AsyncSession,
    client_name: str,
    allowed_domain: str | None = None,
) -> tuple[str, AccessAPIKey]:
    """
    Generate a new API key for a third-party client application.
    Returns (raw_secret_key, db_record). The raw_secret_key should be displayed ONCE to the user.
    """
    raw_token = secrets.token_urlsafe(32)
    raw_key = f"gw_live_{raw_token}"
    key_prefix = raw_key[:KEY_PREFIX_LENGTH]
    hashed = hash_key(raw_key)

    # Clean domain if provided
    domain = allowed_domain.strip().lower() if allowed_domain and allowed_domain.strip() else None

    api_key_record = AccessAPIKey(
        client_name=client_name.strip(),
        allowed_domain=domain,
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
    if not raw_key or not raw_key.strip():
        return None

    hashed = hash_key(raw_key.strip())
    stmt = select(AccessAPIKey).where(
        AccessAPIKey.hashed_key == hashed,
        AccessAPIKey.is_active == True,
    )
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if not record:
        logger.warning("API key verification failed: Key not found or inactive")
        return None

    # Domain restriction check if record.allowed_domain is configured (and not "*")
    if record.allowed_domain and record.allowed_domain != "*":
        if not origin_domain:
            logger.warning("API key domain mismatch: Key requires domain '%s' but no Origin/Referer provided", record.allowed_domain)
            return None
        
        # Strip protocol and port from origin domain for comparison
        clean_origin = origin_domain.lower().replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        clean_allowed = record.allowed_domain.lower().replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]

        if clean_origin != clean_allowed and not clean_origin.endswith("." + clean_allowed):
            logger.warning("API key domain mismatch: Allowed '%s', Got '%s'", clean_allowed, clean_origin)
            return None

    # Update last_used_at
    record.last_used_at = datetime.datetime.now(datetime.timezone.utc)
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
