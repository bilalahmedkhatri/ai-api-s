"""Durable chunk checkpoints with fenced, renewable worker leases."""

import asyncio
import contextlib
import datetime
import io
import json
import logging
import time
import uuid
import wave

from sqlalchemy import or_, select, update

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.db_models import TTSChunk, TTSJob
from app.services.generation_telemetry import (
    attempt_summary,
    object_data,
    persist_attempt,
    telemetry_scope,
)
from app.services.media_extractor import get_b2_session
from app.services.tts.audio_utils import get_wav_stats, merge_wav_bytes
from app.services.tts.generator import generate_dynamic_speech
from app.services.tts.notifier import notify_frontend
from app.services.voice_url_service import generate_audio_presigned_url

logger = logging.getLogger(__name__)
# Process-wide bounds, shared by all background jobs in this worker.
_limits = {
    'gemini': asyncio.Semaphore(settings.tts_gemini_concurrency),
    'kokoro': asyncio.Semaphore(settings.tts_kokoro_concurrency),
}


def _now():
    return datetime.datetime.now(datetime.UTC)


def lease_deadline():
    return _now() + datetime.timedelta(seconds=settings.tts_lease_seconds)


async def claim_retry(db, job_id, app_id, *, expired_only=False):
    token = uuid.uuid4().hex
    eligible = or_(TTSJob.lease_until.is_(None), TTSJob.lease_until < _now())
    if not expired_only:
        eligible = or_(TTSJob.status == 'failed', eligible)
    filters = [
        TTSJob.job_id == job_id, TTSJob.authorized_app_id == app_id,
        TTSJob.status != 'completed', TTSJob.generation_json.is_not(None), eligible,
    ]
    if expired_only:
        filters.append(TTSJob.status == 'processing')
    result = await db.execute(update(TTSJob).where(*filters).values(status='processing', run_token=token, lease_until=lease_deadline(),
             error_message=None, updated_at=_now()).execution_options(synchronize_session='fetch'))
    await db.commit()
    return token if result.rowcount == 1 else None


def safe_error(exc):
    # Provider bodies/URLs may contain credentials or input text; never persist them.
    if 'GEMINI_QUOTA_EXCEEDED' in str(exc):
        return 'GEMINI_QUOTA_EXCEEDED: provider quota exhausted; check quota before retry.'
    return f'{type(exc).__name__}: chunk generation/storage failed; retry after checking service availability.'


def _validate_audio(audio):
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        if wav.getnframes() < 1 or wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError('Expected nonempty mono 16-bit PCM WAV')
        if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
            raise ValueError('Truncated PCM WAV')
        return wav.getframerate()


async def _job_update(job_id, token, **values):
    async with AsyncSessionLocal() as db:
        if values.get('status') in {'completed', 'failed'}:
            job = (await db.execute(select(TTSJob).where(
                TTSJob.job_id == job_id, TTSJob.run_token == token, TTSJob.status == 'processing',
            ).with_for_update())).scalar_one_or_none()
            if job is None:
                raise RuntimeError('Worker lease no longer owned')
            data = object_data(job.generation_json)
            telemetry = data.setdefault('_telemetry', {})
            finished = _now()
            created = job.created_at.replace(tzinfo=datetime.UTC) if job.created_at.tzinfo is None else job.created_at
            telemetry.update(finished_at=finished.isoformat(),
                             duration_ms=round((finished - created).total_seconds() * 1000, 1))
            telemetry.update(attempt_summary(telemetry))
            values['generation_json'] = json.dumps(data)
        result = await db.execute(update(TTSJob).where(
            TTSJob.job_id == job_id, TTSJob.run_token == token,
            TTSJob.status == 'processing',
        ).values(**values))
        await db.commit()
        if result.rowcount != 1:
            raise RuntimeError('Worker lease no longer owned')


async def _chunk_update(job_id, token, index, **values):
    async with AsyncSessionLocal() as db:
        # Conditional write fences workers superseded by a retry after lease expiry.
        owned = select(TTSJob.job_id).where(
            TTSJob.job_id == job_id, TTSJob.run_token == token, TTSJob.status == 'processing',
        )
        result = await db.execute(update(TTSChunk).where(
            TTSChunk.job_id.in_(owned), TTSChunk.chunk_index == index,
        ).values(**values))
        await db.commit()
        if result.rowcount != 1:
            raise RuntimeError('Worker lease no longer owned')


async def _heartbeat(job_id, token):
    while True:
        await asyncio.sleep(60)
        await _job_update(job_id, token, lease_until=lease_deadline(), updated_at=_now())


async def _put(key, data):
    session, config = get_b2_session()
    async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=config) as s3:
        await s3.put_object(Bucket=settings.b2_bucket_name, Key=key, Body=data, ContentType='audio/wav')


async def _get(key):
    session, config = get_b2_session()
    async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=config) as s3:
        response = await s3.get_object(Bucket=settings.b2_bucket_name, Key=key)
        async with response['Body'] as body:
            return await body.read()


async def _cleanup(keys, job_id):
    try:
        session, config = get_b2_session()
        async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=config) as s3:
            for key in keys:
                await s3.delete_object(Bucket=settings.b2_bucket_name, Key=key)
    except Exception as exc:
        logger.warning('TTS cleanup deferred job=%s error_type=%s', job_id, type(exc).__name__)


async def process_tts_job(job_id, model_name=None, text=None, voice=None, lang=None,
                          extra_params=None, provider=None, run_token=None):
    """Run persisted settings. Legacy positional arguments remain import-call compatible.

    Successful checkpoints survive retries. Expired workers are claimed by the
    lifespan recovery service or an explicit app-owned retry, using the same fence.
    """
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(TTSJob).where(TTSJob.job_id == job_id))).scalar_one_or_none()
        if not job or not run_token or job.run_token != run_token or job.status != 'processing':
            return
        generation = json.loads(job.generation_json)
        rows = (await db.execute(select(TTSChunk).where(TTSChunk.job_id == job_id)
                                 .order_by(TTSChunk.chunk_index))).scalars().all()
        chunks = [(c.chunk_index, c.text, c.status, c.object_key) for c in rows]
        app_id = job.authorized_app_id
        char_count = len(job.text)
    model = generation['model_snapshot']
    provider = model['provider'].lower()
    event = {'job_id': job_id, 'model': model['name'], 'voice': generation['voice'],
             'provider': provider, 'char_count': char_count, 'authorized_app_id': app_id}
    heartbeat = asyncio.create_task(_heartbeat(job_id, run_token))
    tasks = []
    group = None
    telemetry_lock = asyncio.Lock()

    async def save_attempt(event):
        async with telemetry_lock:
            await persist_attempt(AsyncSessionLocal, TTSJob, [TTSJob.job_id == job_id,
                TTSJob.run_token == run_token, TTSJob.status == 'processing'], 'generation_json', event)

    async def synthesize(chunk):
        index, chunk_text, state, key = chunk
        if state == 'completed' and key:
            return
        async with _limits[provider]:
            started = time.monotonic()
            await _chunk_update(job_id, run_token, index, status='processing',
                                attempts=TTSChunk.attempts + 1, error_message=None)
            try:
                logger.info('TTS chunk started job=%s index=%d provider=%s', job_id, index, provider)
                with telemetry_scope(save_attempt, model=model['name'], provider=provider, operation_id=f'chunk:{index}'):
                    audio, _ = await generate_dynamic_speech(
                        model_name=model['name'], text=chunk_text, voice=generation['voice'],
                        lang=generation['lang'], speed=generation['speed'],
                        extra_params=generation['extra_params'], provider=provider, model_snapshot=model,
                    )
                _validate_audio(audio)
                key = f'tts_audio/chunks/{job_id}/{run_token}/{index:04d}.wav'
                await _put(key, audio)
                await _chunk_update(job_id, run_token, index, status='completed', object_key=key,
                                    frame_count=get_wav_stats(audio)['frame_count'])
                logger.info('TTS chunk completed job=%s index=%d provider=%s elapsed_sec=%.2f',
                            job_id, index, provider, time.monotonic() - started)
            except Exception as exc:
                reason = safe_error(exc)
                logger.warning('TTS chunk failed job=%s index=%d provider=%s reason=%s', job_id, index, provider, reason)
                await _chunk_update(job_id, run_token, index, status='failed', error_message=reason)

    try:
        if not chunks:
            raise ValueError('Job has no chunk checkpoints')
        tasks = [asyncio.create_task(synthesize(c)) for c in chunks]
        group = asyncio.gather(*tasks)
        done, _ = await asyncio.wait({group, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            await heartbeat  # Lease renewal failed; stop this worker.
        await group
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(TTSChunk).where(TTSChunk.job_id == job_id)
                                     .order_by(TTSChunk.chunk_index))).scalars().all()
            states = [(c.chunk_index, c.status, c.object_key, c.error_message, c.frame_count) for c in rows]
        failed = [c for c in states if c[1] != 'completed' or not c[2]]
        if failed:
            raise RuntimeError(failed[0][3] or 'Incomplete chunk checkpoint')
        audio_parts = []
        sample_rate = None
        for index, _, key, _, frames in states:
            try:
                audio = await _get(key)
                rate = _validate_audio(audio)
                if sample_rate is not None and rate != sample_rate:
                    raise ValueError('Chunk sample rate differs from prior audio')
                sample_rate = rate
                if frames is None:
                    await _chunk_update(job_id, run_token, index, frame_count=get_wav_stats(audio)['frame_count'])
                audio_parts.append(audio)
            except Exception as exc:
                await _chunk_update(job_id, run_token, index, status='failed', error_message=safe_error(exc))
                raise
        audio = merge_wav_bytes(audio_parts, silence_ms=200 if provider == 'kokoro' else 0)
        stats = get_wav_stats(audio)
        stats['size_mb'] = round(stats['size_bytes'] / (1024 * 1024), 3)
        key = f'tts_audio/tts_{job_id}_{run_token[:8]}.wav'
        await _put(key, audio)
        url = await generate_audio_presigned_url(key)
        await _job_update(job_id, run_token, status='completed', audio_url=url, object_key=key,
                          duration_sec=stats['duration_sec'], size_bytes=stats['size_bytes'],
                          error_message=None, run_token=None, lease_until=None, updated_at=_now())
        logger.info('TTS job completed job=%s chunks=%d duration_sec=%.2f size_bytes=%d',
                    job_id, len(states), stats['duration_sec'], stats['size_bytes'])
        # Delete only after final upload, presigning and completed DB commit succeeded.
        await _cleanup([c[2] for c in states], job_id)
        await notify_frontend({**event, 'event': 'tts.job.completed', 'status': 'completed',
                               'audio_url': url, 'object_key': key, **stats,
                               'error': None, 'timestamp': _now().isoformat()})
    except Exception as exc:
        reason = safe_error(exc)
        logger.error('TTS job failed job=%s provider=%s reason=%s', job_id, provider, reason)
        with contextlib.suppress(Exception):
            await _job_update(job_id, run_token, status='failed', error_message=reason,
                              run_token=None, lease_until=None, updated_at=_now())
            await notify_frontend({**event, 'event': 'tts.job.failed', 'status': 'failed',
                                   'audio_url': None, 'duration_sec': None, 'size_bytes': None,
                                   'error': reason, 'timestamp': _now().isoformat()})
    finally:
        heartbeat.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(heartbeat, *tasks, return_exceptions=True)
        if group is not None:
            await asyncio.gather(group, return_exceptions=True)
