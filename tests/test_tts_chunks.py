"""Indexed TTS regression tests: SQLite + in-memory storage, no provider/network calls."""

import asyncio
import datetime
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.auth import get_current_api_key
from app.models.db_models import TTSChunk, TTSJob, TTSModel
from app.services.tts import jobs
from app.services.tts.audio_utils import pcm_to_wav
from fastapi import Request
from main import app
from sqlalchemy import select

from tests.test_app_authorization import headers


class Storage:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.fail_final = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def put_object(self, Key, Body, **kwargs):
        if self.fail_final and '/chunks/' not in Key:
            raise RuntimeError('storage unavailable')
        self.objects[Key] = Body

    async def get_object(self, Key, **kwargs):
        data = self.objects[Key]
        class Body:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def read(self):
                return data
        return {'Body': Body()}

    async def delete_object(self, Key, **kwargs):
        self.deleted.append(Key)
        self.objects.pop(Key, None)


@pytest.fixture
def context(unit_db, monkeypatch):
    storage = Storage()
    monkeypatch.setattr(jobs, 'AsyncSessionLocal', lambda: unit_db)
    monkeypatch.setattr(jobs, 'get_b2_session', lambda: (SimpleNamespace(client=lambda *a, **k: storage), None))
    presign = AsyncMock(return_value='https://storage.test/audio.wav')
    monkeypatch.setattr(jobs, 'generate_audio_presigned_url', presign)
    monkeypatch.setattr('app.services.voice_url_service.generate_audio_presigned_url', presign)
    notify = AsyncMock()
    monkeypatch.setattr(jobs, 'notify_frontend', notify)
    auth_state = SimpleNamespace(id=1, authorized_app_id=1)
    async def auth(request: Request):
        return auth_state
    app.dependency_overrides[get_current_api_key] = auth
    monkeypatch.setattr(jobs, '_limits', {'gemini': asyncio.Semaphore(2), 'kokoro': asyncio.Semaphore(1)})
    return unit_db, storage, notify, auth_state


async def seed(db, count=3, provider='gemini'):
    generation = {'model_snapshot': {
        'name': 'test', 'provider': provider, 'is_local': False,
        'api_endpoint_url': 'https://provider.test/tts', 'provider_config': {},
        'default_voice': 'Kore', 'default_speed': 1, 'default_lang': 'en-us',
    }, 'voice': 'Kore', 'lang': 'en-us', 'speed': 1.2, 'extra_params': {}}
    job = TTSJob(job_id='job', text='script', status='processing', authorized_app_id=1,
                 generation_json=json.dumps(generation), run_token='first', lease_until=jobs.lease_deadline())
    db.add(job)
    await db.flush()
    for i in range(count):
        db.add(TTSChunk(job_id='job', chunk_index=i, text=str(i), status='pending', attempts=0))
    await db.commit()
    return job


async def run(job, token='first'):
    await jobs.process_tts_job(job.job_id, run_token=token)


def wav(index):
    return pcm_to_wav(bytes([index + 1, 0]) * 240)


async def test_parallel_order_and_cleanup(context, monkeypatch):
    db, storage, notify, _ = context
    job = await seed(db)
    active = peak = 0
    completed = []
    async def generate(**kwargs):
        nonlocal active, peak
        index = int(kwargs['text'])
        assert kwargs['speed'] == 1.2
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02 if index == 0 else .001)
        active -= 1
        completed.append(index)
        return wav(index), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    await run(job)
    await db.refresh(job)
    assert job.status == 'completed' and peak == 2 and completed != [0, 1, 2]
    with wave.open(io.BytesIO(storage.objects[job.object_key]), 'rb') as audio:
        assert audio.readframes(audio.getnframes()) == b''.join(bytes([i + 1, 0]) * 240 for i in range(3))
    assert len(storage.deleted) == 3 and len(storage.objects) == 1
    assert notify.call_args.args[0]['event'] == 'tts.job.completed'


async def test_retry_only_failed_chunk(context, monkeypatch):
    db, storage, notify, _ = context
    job = await seed(db)
    calls = []
    async def generate(**kwargs):
        index = int(kwargs['text'])
        calls.append(index)
        if index == 1:
            raise RuntimeError('GEMINI_QUOTA_EXCEEDED: secret-body')
        return wav(index), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    await run(job)
    await db.refresh(job)
    assert job.status == 'failed' and 'GEMINI_QUOTA_EXCEEDED' in job.error_message
    assert 'secret-body' not in job.error_message and storage.deleted == []
    calls.clear()
    async def recover(**kwargs):
        calls.append(int(kwargs['text']))
        return wav(1), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', recover)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/audio/tts/retry/job')
        assert response.status_code == 202
        status = (await client.get('/api/v1/audio/tts/status/job')).json()
        assert status['status'] == 'completed' and status['chunks']['completed'] == 3
        assert status['duration_seconds'] > 0 and status['size_bytes'] > 44
        assert [c['attempts'] for c in status['chunks']['items']] == [1, 2, 1]
    assert calls == [1] and len(storage.deleted) == 3
    assert notify.await_count == 2


async def test_failed_merge_upload_reuses_all_checkpoints(context, monkeypatch):
    db, storage, _, _ = context
    job = await seed(db, count=2)
    generate = AsyncMock(side_effect=lambda **kwargs: (wav(int(kwargs['text'])), 'audio/wav'))
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    storage.fail_final = True
    await run(job)
    await db.refresh(job)
    assert job.status == 'failed' and not storage.deleted
    storage.fail_final = False
    job.status, job.run_token = 'processing', 'second'
    await db.commit()
    await run(job, 'second')
    await db.refresh(job)
    assert job.status == 'completed' and generate.await_count == 2


@pytest.mark.parametrize('path', ['/tts/status/job', '/tts/retry/job', '/tts/renew-url'])
async def test_foreign_and_legacy_job_blocked(context, monkeypatch, path):
    db, _, _, auth = context
    job = await seed(db)
    presign = AsyncMock()
    monkeypatch.setattr('app.services.voice_url_service.generate_audio_presigned_url', presign)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for owner in (2, None):
            job.authorized_app_id = owner
            await db.commit()
            if 'status' in path:
                response = await client.get('/api/v1/audio' + path)
            else:
                response = await client.post('/api/v1/audio' + path, json={'job_id': 'job'})
            assert response.status_code == 404
        response = await client.post('/api/v1/audio/tts/renew-url', json={'url': 'tts_audio/arbitrary.wav'})
        assert response.status_code == 404
    presign.assert_not_awaited()


async def test_retry_running_and_completed_rejected_and_expired_recovered(context, monkeypatch):
    db, _, _, _ = context
    job = await seed(db, count=1)
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', AsyncMock(return_value=(wav(0), 'audio/wav')))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/v1/audio/tts/retry/job')).status_code == 409
        job.lease_until = jobs._now() - datetime.timedelta(seconds=1)
        await db.commit()
        assert (await client.post('/api/v1/audio/tts/retry/job')).status_code == 202
        assert (await client.post('/api/v1/audio/tts/retry/job')).status_code == 409


async def test_superseded_worker_cannot_write(context, monkeypatch):
    db, storage, _, _ = context
    job = await seed(db, count=1)
    generate = AsyncMock(return_value=(wav(0), 'audio/wav'))
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    await run(job, 'obsolete')
    generate.assert_not_awaited()
    with pytest.raises(RuntimeError, match='lease'):
        await jobs._chunk_update('job', 'obsolete', 0, status='completed')
    assert not storage.objects


async def test_invalid_audio_is_not_checkpointed(context, monkeypatch):
    db, storage, _, _ = context
    job = await seed(db, count=1)
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', AsyncMock(return_value=(b'bad-audio', 'audio/wav')))
    await run(job)
    await db.refresh(job)
    assert job.status == 'failed' and not storage.objects


async def test_real_gateway_permissions_and_ownership(onboarding_db, monkeypatch):
    db, _, apps, _ = onboarding_db
    app.dependency_overrides.pop(get_current_api_key, None)
    for registered in apps:
        registered.allowed_endpoints_json = json.dumps([
            'GET /api/v1/audio/tts/status/{job_id}', 'POST /api/v1/audio/tts/retry/{job_id}',
        ])
    job = TTSJob(job_id='private', text='private text', status='failed', authorized_app_id=apps[0].id)
    db.add(job)
    await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        first, second = await headers(db, apps[0]), await headers(db, apps[1])
        assert (await client.get('/api/v1/audio/tts/status/private', headers=first)).status_code == 200
        assert (await client.get('/api/v1/audio/tts/status/private', headers=second)).status_code == 404
        assert (await client.post('/api/v1/audio/tts/retry/private', headers=second)).status_code == 404
        apps[0].allowed_endpoints_json = json.dumps(['GET /api/v1/audio/tts/status/{job_id}'])
        await db.commit()
        assert (await client.post('/api/v1/audio/tts/retry/private', headers=first)).status_code == 403


async def test_api_creates_indexed_chunks_and_snapshots_speed(context, monkeypatch):
    db, _, _, _ = context
    model = TTSModel(name='snapshot', provider='kokoro', is_local=True, is_active=True,
                     default_voice='af_sarah', default_speed=1, provider_config={})
    db.add(model)
    await db.commit()
    worker = AsyncMock()
    monkeypatch.setattr('app.services.tts_service.process_tts_job', worker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/audio/tts', json={'model': 'snapshot', 'text': 'a' * 900, 'speed': 1.3})
        assert response.status_code == 200
    job = (await db.execute(select(TTSJob))).scalar_one()
    chunks = (await db.execute(select(TTSChunk).order_by(TTSChunk.chunk_index))).scalars().all()
    assert [len(c.text) for c in chunks] == [400, 400, 100]
    assert job.authorized_app_id == 1 and json.loads(job.generation_json)['speed'] == 1.3
    assert worker.call_args.kwargs['run_token'] == job.run_token

async def test_lost_checkpoint_regenerates_only_missing_chunk(context, monkeypatch):
    db, storage, _, _ = context
    job = await seed(db, count=2)
    generate = AsyncMock(side_effect=lambda **kwargs: (wav(int(kwargs['text'])), 'audio/wav'))
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    storage.fail_final = True
    await run(job)
    chunks = (await db.execute(select(TTSChunk).order_by(TTSChunk.chunk_index))).scalars().all()
    storage.objects.pop(chunks[0].object_key)
    storage.fail_final = False
    job.status, job.run_token = 'processing', 'second'
    await db.commit()
    await run(job, 'second')
    await db.refresh(chunks[0])
    assert chunks[0].status == 'failed' and generate.await_count == 2
    job.status, job.run_token = 'processing', 'third'
    await db.commit()
    await run(job, 'third')
    await db.refresh(job)
    assert job.status == 'completed' and generate.await_count == 3
    assert generate.call_args.kwargs['text'] == '0'


async def test_worker_fenced_when_replaced_during_generation(context, monkeypatch):
    db, storage, notify, _ = context
    job = await seed(db, count=1)
    async def generate(**kwargs):
        job.run_token = 'replacement'
        await db.commit()
        return wav(0), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    await run(job)
    await db.refresh(job)
    assert job.run_token == 'replacement' and job.status == 'processing'
    notify.assert_not_awaited()


async def test_cleanup_failure_does_not_fail_completed_job(context, monkeypatch):
    db, storage, notify, _ = context
    job = await seed(db, count=1)
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', AsyncMock(return_value=(wav(0), 'audio/wav')))
    storage.delete_object = AsyncMock(side_effect=RuntimeError('delete unavailable'))
    await run(job)
    await db.refresh(job)
    assert job.status == 'completed' and notify.call_args.args[0]['status'] == 'completed'


def test_chunk_migration_preserves_legacy_and_roundtrips():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('tts_migration', Path(__file__).resolve().parents[1] / 'alembic/versions/e42b8d901c63_tts_chunk_checkpoints.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE authorized_apps (id INTEGER PRIMARY KEY)'))
        connection.execute(sa.text('CREATE TABLE tts_jobs (id INTEGER PRIMARY KEY, job_id VARCHAR(64) UNIQUE, text TEXT)'))
        connection.execute(sa.text("INSERT INTO tts_jobs VALUES (1, 'legacy', 'kept')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            columns = {c['name'] for c in sa.inspect(connection).get_columns('tts_jobs')}
            assert {'generation_json', 'authorized_app_id', 'lease_until', 'run_token'} <= columns
            assert connection.execute(sa.text('SELECT text, authorized_app_id FROM tts_jobs')).one() == ('kept', None)
            connection.execute(sa.text("INSERT INTO tts_chunks (job_id, chunk_index, text, status, attempts) VALUES ('legacy', 0, 'part', 'pending', 0)"))
            migration.downgrade()
            assert 'tts_chunks' not in sa.inspect(connection).get_table_names()
            assert connection.execute(sa.text('SELECT text FROM tts_jobs')).scalar() == 'kept'
    engine.dispose()

async def test_overlapping_retry_starts_one_worker(context, monkeypatch):
    db, _, _, _ = context
    job = await seed(db, count=1)
    job.status, job.run_token, job.lease_until = 'failed', None, None
    await db.commit()
    started, release = asyncio.Event(), asyncio.Event()
    async def generate(**kwargs):
        started.set()
        await release.wait()
        return wav(0), 'audio/wav'
    generate_mock = AsyncMock(side_effect=generate)
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate_mock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        first = asyncio.create_task(client.post('/api/v1/audio/tts/retry/job'))
        await asyncio.wait_for(started.wait(), 2)
        try:
            assert (await client.post('/api/v1/audio/tts/retry/job')).status_code == 409
        finally:
            release.set()
        assert (await first).status_code == 202
    generate_mock.assert_awaited_once()


async def test_presign_failure_keeps_checkpoints(context, monkeypatch):
    db, storage, _, _ = context
    job = await seed(db, count=1)
    generate = AsyncMock(return_value=(wav(0), 'audio/wav'))
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    monkeypatch.setattr(jobs, 'generate_audio_presigned_url', AsyncMock(side_effect=RuntimeError('presign failed')))
    await run(job)
    await db.refresh(job)
    assert job.status == 'failed' and not storage.deleted
    monkeypatch.setattr(jobs, 'generate_audio_presigned_url', AsyncMock(return_value='https://storage.test/audio.wav'))
    job.status, job.run_token = 'processing', 'second'
    await db.commit()
    await run(job, 'second')
    await db.refresh(job)
    assert job.status == 'completed' and generate.await_count == 1


async def test_heartbeat_failure_cancels_generation(context, monkeypatch):
    db, storage, _, _ = context
    job = await seed(db, count=1)
    started = asyncio.Event()
    async def heartbeat(*args):
        await started.wait()
        raise RuntimeError('database heartbeat failed')
    async def generate(**kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(jobs, '_heartbeat', heartbeat)
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    await asyncio.wait_for(run(job), 2)
    await db.refresh(job)
    assert job.status == 'failed' and not storage.objects


async def test_mismatched_audio_rate_marks_specific_chunk_failed(context, monkeypatch):
    db, _, _, _ = context
    job = await seed(db, count=2)
    async def generate(**kwargs):
        index = int(kwargs['text'])
        return pcm_to_wav(b'\x00\x00' * 240, sample_rate=24000 if index == 0 else 16000), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', generate)
    await run(job)
    rows = (await db.execute(select(TTSChunk).order_by(TTSChunk.chunk_index))).scalars().all()
    assert [c.status for c in rows] == ['completed', 'failed']
