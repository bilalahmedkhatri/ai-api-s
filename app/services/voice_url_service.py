"""app/services/voice_url_service.py — Service for Backblaze B2 S3 Pre-signed URL generation, verification, and renewal."""

import datetime
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db_models import TTSJob
from app.services.media_extractor import get_b2_session

logger = logging.getLogger(__name__)

B2_PRESIGNED_EXPIRATION_SECONDS = 604800  # 7 days (Backblaze/AWS S3 maximum allowed)


async def generate_audio_presigned_url(object_key: str, expires_in: int = B2_PRESIGNED_EXPIRATION_SECONDS) -> str:
    """Generate a 7-day S3 pre-signed GET URL for a given Backblaze B2 object key."""
    session, b2_config = get_b2_session()
    async with session.client("s3", endpoint_url=settings.b2_endpoint_url, config=b2_config) as s3:
        url = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.b2_bucket_name, "Key": object_key},
            ExpiresIn=expires_in,
        )
        return url


def extract_object_key_from_url(url_or_key: str) -> str | None:
    """Extract clean object_key (e.g. tts_audio/tts_xxx.wav) from any B2 URL format."""
    if not url_or_key:
        return None

    cleaned = unquote(url_or_key).strip()

    # If it's already just an object key
    if cleaned.startswith("tts_audio/") and "?" not in cleaned:
        return cleaned

    # Extract tts_audio/... path
    if "tts_audio/" in cleaned:
        sub = cleaned[cleaned.find("tts_audio/"):]
        return sub.split("?")[0]

    # Fallback to URL path parsing
    parsed = urlparse(cleaned)
    path = parsed.path.lstrip("/")
    if path.startswith("file/"):
        path = path[len("file/"):]
    bucket_prefix = f"{settings.b2_bucket_name}/"
    if path.startswith(bucket_prefix):
        path = path[len(bucket_prefix):]

    return path.split("?")[0] if path else None


def is_presigned_url_expired(url: str, buffer_seconds: int = 7200) -> bool:
    """
    Check if an S3 pre-signed URL is expired or close to expiring (within buffer_seconds, default 2 hours).
    Returns True if expired, unparseable, or using a broken domain (e.g. f3.backblazeb2.com).
    """
    if not url:
        return True

    # If the URL has the broken f3 domain or lacks S3 signature params, treat as expired
    if "f3.backblazeb2.com" in url or "X-Amz-Signature=" not in url:
        return True

    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        date_str = query.get("X-Amz-Date", [None])[0]
        expires_str = query.get("X-Amz-Expires", [None])[0]

        if not date_str or not expires_str:
            return True

        created_at = datetime.datetime.strptime(date_str, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.UTC
        )
        expires_in = int(expires_str)
        expiration_time = created_at + datetime.timedelta(seconds=expires_in)

        # Check against current UTC time with safety buffer
        now = datetime.datetime.now(datetime.UTC)
        return now >= (expiration_time - datetime.timedelta(seconds=buffer_seconds))
    except Exception as exc:
        logger.warning("Failed to parse presigned URL expiration: %s", exc)
        return True


async def renew_tts_audio_url(
    db: AsyncSession,
    job_id: str | None = None,
    url: str | None = None,
) -> dict:
    """
    Renew a TTS audio pre-signed URL for 7 days.
    Finds the job by job_id or extracts object_key from url, generates a new presigned URL,
    updates the database record with the new URL and updated_at timestamp, and returns the result.
    """
    if not job_id and not url:
        raise ValueError("Either 'job_id' or 'url' must be provided.")

    target_job: TTSJob | None = None
    target_object_key: str | None = None

    if job_id:
        result = await db.execute(select(TTSJob).where(TTSJob.job_id == job_id.strip()))
        target_job = result.scalar_one_or_none()

    if not target_job and url:
        target_object_key = extract_object_key_from_url(url)
        if target_object_key:
            # Match UUID in filename if present
            uuid_match = re.search(r"tts_([a-f0-9\-]{36})_", target_object_key)
            if uuid_match:
                extracted_job_id = uuid_match.group(1)
                result = await db.execute(select(TTSJob).where(TTSJob.job_id == extracted_job_id))
                target_job = result.scalar_one_or_none()

            # If still not found, search by audio_url containing the object key
            if not target_job:
                result = await db.execute(
                    select(TTSJob).where(TTSJob.audio_url.ilike(f"%{target_object_key}%"))
                )
                target_job = result.scalar_one_or_none()

    if target_job:
        target_object_key = extract_object_key_from_url(target_job.audio_url) or target_object_key

    if not target_object_key:
        raise ValueError("Could not determine Backblaze object key for the given audio.")

    # Generate new 7-day pre-signed URL
    new_audio_url = await generate_audio_presigned_url(target_object_key)
    now_utc = datetime.datetime.now(datetime.UTC)

    # Update database record if job exists
    resolved_job_id = target_job.job_id if target_job else job_id or "unknown"
    if target_job:
        target_job.audio_url = new_audio_url
        target_job.updated_at = now_utc
        await db.commit()
        await db.refresh(target_job)
        logger.info("Renewed 7-day presigned URL for job_id=%s", target_job.job_id)

    return {
        "status": "success",
        "job_id": resolved_job_id,
        "audio_url": new_audio_url,
        "updated_at": now_utc.isoformat(),
    }


async def ensure_valid_audio_url(job: TTSJob, db: AsyncSession) -> str:
    """
    Helper for status endpoint: returns job.audio_url if still valid,
    or automatically renews and updates the DB if expired.
    """
    if not job.audio_url:
        return ""

    if is_presigned_url_expired(job.audio_url):
        renewed = await renew_tts_audio_url(db=db, job_id=job.job_id, url=job.audio_url)
        return renewed["audio_url"]

    return job.audio_url
