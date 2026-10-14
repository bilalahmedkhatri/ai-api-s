"""Ordered NDJSON previews, including readers that arrive after chunk cleanup."""

import asyncio
import base64
import io
import json
import wave

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.db_models import TTSChunk, TTSJob
from app.services.tts.audio_utils import pcm_to_wav
from app.services.tts.jobs import _get, _now, safe_error


async def snapshot(job_id, app_id):
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(TTSJob).where(
            TTSJob.job_id == job_id, TTSJob.authorized_app_id == app_id,
        ))).scalar_one_or_none()
        if not job:
            return None, []
        rows = (await db.execute(select(TTSChunk).where(TTSChunk.job_id == job_id)
                                 .order_by(TTSChunk.chunk_index))).scalars().all()
        return {'status': job.status, 'object_key': job.object_key, 'error': job.error_message,
                'lease_until': job.lease_until, 'generation': json.loads(job.generation_json or '{}')}, [
                    {'index': c.chunk_index, 'status': c.status, 'key': c.object_key,
                     'frames': c.frame_count} for c in rows]


def _slice_final(audio, chunks, index, kokoro):
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        gap = int(wav.getframerate() * .2) if kokoro else 0
        offset = sum(c['frames'] for c in chunks[:index]) + index * gap
        wav.setpos(offset)
        return pcm_to_wav(wav.readframes(chunks[index]['frames']), sample_rate=wav.getframerate())


def _event(payload):
    return json.dumps(payload, separators=(',', ':')) + '\n'


async def stream_chunks(job_id, app_id, after=0):
    """Never emit a later ready chunk before the next required index."""
    index, final_audio, kokoro = after, None, False
    try:
        while True:
            job, chunks = await snapshot(job_id, app_id)
            if not job:
                yield _event({'event': 'error', 'error': 'Audio job is no longer accessible.'})
                return
            while index < len(chunks) and chunks[index]['status'] == 'completed':
                chunk = chunks[index]
                try:
                    if final_audio is None:
                        audio = await _get(chunk['key'])
                    else:
                        audio = _slice_final(final_audio, chunks, index, kokoro)
                except Exception:
                    # Final completion may delete checkpoints between snapshot and read.
                    job, chunks = await snapshot(job_id, app_id)
                    if not job or job['status'] != 'completed' or any(c['frames'] is None for c in chunks):
                        raise
                    final_audio = await _get(job['object_key'])
                    kokoro = job['generation'].get('model_snapshot', {}).get('provider', '').lower() == 'kokoro'
                    audio = _slice_final(final_audio, chunks, index, kokoro)
                yield _event({'event': 'chunk', 'index': index, 'total': len(chunks),
                              'audio': base64.b64encode(audio).decode('ascii')})
                index += 1
            if job['status'] == 'completed':
                yield _event({'event': 'completed', 'next_index': index})
                return
            if job['status'] == 'failed':
                yield _event({'event': 'failed', 'error': job['error'], 'next_index': index})
                return
            deadline = job['lease_until']
            if deadline and deadline.replace(tzinfo=_now().tzinfo) < _now():
                yield _event({'event': 'failed', 'error': 'Worker stopped. Check progress to retry.', 'next_index': index})
                return
            await asyncio.sleep(.5)
    except Exception as exc:
        yield _event({'event': 'error', 'error': safe_error(exc), 'next_index': index})
