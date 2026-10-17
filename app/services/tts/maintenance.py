"""Recover expired jobs and reclaim old unreferenced temporary chunks."""
import asyncio
import contextlib
import datetime
import json
import logging

from sqlalchemy import or_, select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.db_models import AuthorizedApp, TTSChunk, TTSJob, User
from app.services.api_key_service import customer_project_active
from app.services.media_extractor import get_b2_session
from app.services.tts.availability import is_local_kokoro
from app.services.tts.jobs import _now, claim_retry, process_tts_job

logger = logging.getLogger(__name__)


async def recover_expired(capacity=2):
    claims = []
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(TTSJob).join(AuthorizedApp).join(User).where(
            TTSJob.status == 'processing', TTSJob.generation_json.is_not(None),
            or_(TTSJob.lease_until.is_(None), TTSJob.lease_until < _now()),
            AuthorizedApp.is_active.is_(True), AuthorizedApp.is_approved.is_(True),
            User.is_active.is_(True), User.api_access_enabled.is_(True),
        ).order_by(TTSJob.created_at).limit(capacity))).scalars().all()
        for job in rows:
            if not await customer_project_active(db, job.authorized_app_id):
                continue
            # Respect revoked creation permission and reject invalid legacy snapshots.
            try:
                app = await db.get(AuthorizedApp, job.authorized_app_id)
                if 'POST /api/v1/audio/tts' not in json.loads(app.allowed_endpoints_json):
                    continue
                generation = json.loads(job.generation_json)
                model = generation.get('model_snapshot', {})
                if is_local_kokoro(model.get('provider'), model.get('is_local')):
                    continue
                if not {'voice', 'lang', 'speed', 'extra_params'} <= generation.keys() or not generation.get('voice'):
                    continue
                if not model.get('name') or model.get('provider', '').lower() not in {'gemini', 'kokoro'}:
                    continue
            except (ValueError, TypeError, AttributeError):
                continue
            token = await claim_retry(db, job.job_id, job.authorized_app_id, expired_only=True)
            if token:
                claims.append((job.job_id, token))
                logger.info('TTS expired job recovered job=%s', job.job_id)
    return claims


async def cleanup_orphans():
    cutoff = _now() - datetime.timedelta(seconds=max(settings.tts_orphan_age_seconds, settings.tts_lease_seconds * 2))
    session, config = get_b2_session()
    deleted = 0
    async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=config) as s3:
        cursor = {}
        while True:
            page = await s3.list_objects_v2(Bucket=settings.b2_bucket_name, Prefix='tts_audio/chunks/', **cursor)
            # Refresh protection per page. Successful checkpoints of failed jobs survive.
            async with AsyncSessionLocal() as db:
                keys = set((await db.execute(select(TTSChunk.object_key).join(TTSJob).where(
                    TTSJob.status != 'completed', TTSChunk.object_key.is_not(None),
                ))).scalars().all())
                active = set((await db.execute(select(TTSJob.job_id, TTSJob.run_token).where(
                    TTSJob.status == 'processing', TTSJob.run_token.is_not(None),
                ))).all())
            for item in page.get('Contents', []):
                key, modified = item['Key'], item['LastModified']
                parts = key.split('/')
                if len(parts) != 5 or parts[:2] != ['tts_audio', 'chunks'] or not key.endswith('.wav'):
                    continue
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=datetime.UTC)
                if modified >= cutoff or key in keys or (parts[2], parts[3]) in active:
                    continue
                await s3.delete_object(Bucket=settings.b2_bucket_name, Key=key)
                deleted += 1
            if not page.get('IsTruncated'):
                break
            cursor = {'ContinuationToken': page['NextContinuationToken']}
    logger.info('TTS orphan cleanup completed deleted=%d', deleted)
    return deleted


async def maintenance_loop():
    workers = set()
    next_cleanup = 0
    try:
        while True:
            for task in workers:
                if task.done() and not task.cancelled() and task.exception():
                    logger.warning('TTS recovered worker stopped error_type=%s', type(task.exception()).__name__)
            workers = {task for task in workers if not task.done()}
            try:
                if len(workers) < 2:
                    for job_id, token in await recover_expired(2 - len(workers)):
                        workers.add(asyncio.create_task(process_tts_job(job_id, run_token=token)))
                now = asyncio.get_running_loop().time()
                if now >= next_cleanup:
                    next_cleanup = now + 3600
                    await asyncio.wait_for(cleanup_orphans(), timeout=30)
            except Exception as exc:
                logger.warning('TTS maintenance deferred error_type=%s', type(exc).__name__)
            await asyncio.sleep(settings.tts_recovery_interval)
    finally:
        for task in workers:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*workers, return_exceptions=True)
