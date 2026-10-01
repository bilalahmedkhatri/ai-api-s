"""app/services/key_manager.py — DB-backed provider API key rotation service."""

import logging
import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token, encrypt_token
from app.models.db_models import ProviderAPIKey

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

async def add_provider_key(
    db: AsyncSession,
    provider: str,
    plain_key: str,
    label: str,
    priority: int = 1,
    daily_limit: int | None = None,
) -> ProviderAPIKey:
    """
    Encrypt and save a new provider API key.
    Use label to differentiate multiple keys for the same provider
    (e.g. label='account-1', 'account-2').
    """
    encrypted = encrypt_token(plain_key)
    key_obj = ProviderAPIKey(
        provider=provider.lower().strip(),
        label=label.strip(),
        encrypted_key=encrypted,
        priority=priority,
        is_active=True,
        daily_limit=daily_limit,
        requests_today=0,
    )
    db.add(key_obj)
    await db.commit()
    await db.refresh(key_obj)
    logger.info("Added API key for provider=%s label=%s priority=%d", provider, label, priority)
    return key_obj


async def list_provider_keys(db: AsyncSession, provider: str) -> list[dict]:
    """Return all keys for a provider (decrypted key NOT included — only metadata)."""
    result = await db.execute(
        select(ProviderAPIKey)
        .where(ProviderAPIKey.provider == provider.lower())
        .order_by(ProviderAPIKey.priority.asc())
    )
    keys = result.scalars().all()
    return [
        {
            "id": k.id,
            "provider": k.provider,
            "label": k.label,
            "priority": k.priority,
            "is_active": k.is_active,
            "daily_limit": k.daily_limit,
            "requests_today": k.requests_today,
            "is_within_limit": k.is_within_limit,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


async def toggle_key(db: AsyncSession, key_id: int, is_active: bool) -> bool:
    """Enable or disable a key by its ID."""
    result = await db.execute(
        update(ProviderAPIKey)
        .where(ProviderAPIKey.id == key_id)
        .values(is_active=is_active)
    )
    await db.commit()
    return result.rowcount > 0


async def delete_provider_key(db: AsyncSession, key_id: int) -> bool:
    """Permanently delete a key by its ID."""
    from sqlalchemy import delete as sql_delete
    result = await db.execute(
        sql_delete(ProviderAPIKey).where(ProviderAPIKey.id == key_id)
    )
    await db.commit()
    return result.rowcount > 0


async def update_provider_key(
    db: AsyncSession,
    key_id: int,
    provider: str | None = None,
    label: str | None = None,
    plain_key: str | None = None,
    priority: int | None = None,
    daily_limit: int | None = None,
) -> dict | None:
    """
    Partially update a provider API key's metadata.
    - If plain_key is provided it will be re-encrypted before saving.
    - Only fields explicitly passed (non-None) will be updated.
    Returns the updated row as a dict, or None if key_id not found.
    """
    result = await db.execute(select(ProviderAPIKey).where(ProviderAPIKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        return None

    if provider is not None:
        key.provider = provider.strip().lower()
    if label is not None:
        key.label = label.strip()
    if plain_key is not None:
        key.encrypted_key = encrypt_token(plain_key)
    if priority is not None:
        key.priority = priority
    if daily_limit is not None:
        key.daily_limit = daily_limit

    await db.commit()
    await db.refresh(key)
    logger.info("Updated provider key id=%d (provider=%s, label=%s)", key_id, key.provider, key.label)
    return {
        "id": key.id,
        "provider": key.provider,
        "label": key.label,
        "priority": key.priority,
        "is_active": key.is_active,
        "daily_limit": key.daily_limit,
        "requests_today": key.requests_today,
        "is_within_limit": key.is_within_limit,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "created_at": key.created_at.isoformat(),
    }


async def reset_daily_counts(db: AsyncSession, provider: str | None = None) -> int:
    """
    Reset requests_today to 0 for all keys (or a specific provider).
    Call this once per day via a scheduled cron.
    Also re-enables keys that were disabled only due to quota exhaustion.
    """
    filters = []
    if provider:
        filters.append(ProviderAPIKey.provider == provider.lower())

    result = await db.execute(
        update(ProviderAPIKey)
        .where(*filters)
        .values(requests_today=0, is_active=True)
    )
    await db.commit()
    count = result.rowcount
    logger.info("Reset daily counts for %d keys (provider=%s)", count, provider or "all")
    return count


# ---------------------------------------------------------------------------
# Key rotation logic
# ---------------------------------------------------------------------------

async def get_next_available_key(db: AsyncSession, provider: str) -> tuple[int, str] | None:
    """
    Returns (key_id, plain_api_key) of the highest-priority available key
    for the given provider. Returns None if no usable key exists.

    A key is 'available' when:
      - is_active = True
      - daily_limit is NULL OR requests_today < daily_limit
    """
    result = await db.execute(
        select(ProviderAPIKey)
        .where(
            ProviderAPIKey.provider == provider.lower(),
            ProviderAPIKey.is_active.is_(True),
        )
        .order_by(ProviderAPIKey.priority.asc())
    )
    keys = result.scalars().all()

    for k in keys:
        if k.is_within_limit:
            try:
                plain = decrypt_token(k.encrypted_key)
                return (k.id, plain)
            except Exception as exc:
                logger.error("Failed to decrypt key id=%d: %s", k.id, exc)
                continue

    logger.warning("No available API keys for provider=%s", provider)
    return None


async def mark_key_used(db: AsyncSession, key_id: int) -> None:
    """Increment requests_today and update last_used_at after a successful call."""
    await db.execute(
        update(ProviderAPIKey)
        .where(ProviderAPIKey.id == key_id)
        .values(
            requests_today=ProviderAPIKey.requests_today + 1,
            last_used_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await db.commit()


async def mark_key_quota_exceeded(db: AsyncSession, key_id: int) -> None:
    """
    Called when a 429 is received. Sets requests_today = daily_limit
    so the key is skipped on next call. Does NOT permanently disable it —
    it will be re-enabled after midnight reset.
    """
    result = await db.execute(
        select(ProviderAPIKey).where(ProviderAPIKey.id == key_id)
    )
    key = result.scalar_one_or_none()
    if not key:
        return

    if key.daily_limit is not None:
        # Push the counter to the limit so is_within_limit returns False
        await db.execute(
            update(ProviderAPIKey)
            .where(ProviderAPIKey.id == key_id)
            .values(requests_today=key.daily_limit)
        )
    else:
        # No limit set — key is fully exhausted for today, disable it
        await db.execute(
            update(ProviderAPIKey)
            .where(ProviderAPIKey.id == key_id)
            .values(is_active=False)
        )
    await db.commit()
    logger.warning("Marked key id=%d as quota-exceeded for provider rotation.", key_id)
