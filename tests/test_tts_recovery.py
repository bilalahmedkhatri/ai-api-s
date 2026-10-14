"""Recovery/distribution/load checks use SQLite, fake provider keys and B2."""

import asyncio
import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.models.db_models import AuthorizedApp, ProviderAPIKey, TTSChunk, User
from app.services import key_manager
from app.services.tts import gemini_provider, jobs, maintenance
from app.services.tts.audio_utils import get_wav_stats, pcm_to_wav
from app.services.tts.text_utils import split_text_into_chunks
from sqlalchemy import select

from tests.test_tts_chunks import context, run, seed, wav  # noqa: F401


async def test_pool_cycles_eligible_keys_without_changing_other_providers(unit_db, monkeypatch):
    monkeypatch.setattr(key_manager, "decrypt_token", lambda value: value)
    monkeypatch.setattr(gemini_provider, "AsyncSessionLocal", lambda: unit_db)
    monkeypatch.setattr(gemini_provider, "_key_cursor", {})
    for index in range(3):
        unit_db.add(
            ProviderAPIKey(
                provider="gemini",
                label=str(index),
                encrypted_key=f"fake-{index}",
                priority=index + 1,
                requests_today=0,
                is_active=True,
            )
        )
    await unit_db.commit()
    keys = await asyncio.gather(*(gemini_provider._resolve_key(None, "gemini") for _ in range(4)))
    assert [key.value for key in keys] == ["fake-0", "fake-1", "fake-2", "fake-0"]
    await key_manager.mark_key_quota_exceeded(unit_db, keys[1].key_id)
    assert (await gemini_provider._resolve_key(None, "gemini")).value == "fake-2"
    assert (await key_manager.get_next_available_key(unit_db, "gemini"))[1] == "fake-0"


@pytest.mark.parametrize('limit', [1, 2])
async def test_same_key_concurrency_is_bounded_and_different_keys_parallel(monkeypatch, limit):
    monkeypatch.setattr(gemini_provider, "_pace_request", AsyncMock())
    monkeypatch.setattr(gemini_provider, "_key_requests", {})
    monkeypatch.setattr(gemini_provider.settings, "tts_gemini_key_concurrency", limit)
    active, peaks, overall = {}, {}, []

    async def respond(request):
        key = request.headers["x-goog-api-key"]
        active[key] = active.get(key, 0) + 1
        peaks[key] = max(peaks.get(key, 0), active[key])
        overall.append(sum(active.values()))
        await asyncio.sleep(0.01)
        active[key] -= 1
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        results = await asyncio.gather(
            *(
                gemini_provider._synthesize_chunk(
                    client, "https://fake.test", {}, gemini_provider._ActiveKey(key), "gemini"
                )
                for key in ["fake-a", "fake-a", "fake-a", "fake-b"]
            ),
            return_exceptions=True,
        )
    assert all(isinstance(result, RuntimeError) for result in results)
    assert peaks == {"fake-a": limit, "fake-b": 1} and max(overall) == limit + 1


async def test_environment_pool_is_used_without_db_keys(monkeypatch):
    monkeypatch.setattr(gemini_provider, '_next_pool_key', AsyncMock(return_value=None))
    monkeypatch.setattr(gemini_provider.settings, 'gemini_api_keys_raw', 'fake-a,fake-b,fake-a')
    monkeypatch.setattr(gemini_provider, '_fallback_cursor', 0)
    keys = await asyncio.gather(*(gemini_provider._resolve_key(None, 'gemini') for _ in range(3)))
    assert [key.value for key in keys] == ['fake-a', 'fake-b', 'fake-a']


@pytest.mark.parametrize(
    "blocked", [None, "owner", "app", "grant", "active_lease", "failed", "legacy", "snapshot"]
)
async def test_recovery_only_claims_authorized_expired_jobs(onboarding_db, monkeypatch, blocked):
    db, owner, apps, _ = onboarding_db
    app = apps[0]
    app.allowed_endpoints_json = json.dumps(["POST /api/v1/audio/tts"])
    job = await seed(db)
    job.lease_until = jobs._now() - datetime.timedelta(seconds=1)
    if blocked == "owner":
        owner.api_access_enabled = False
    if blocked == "app":
        app.is_approved = False
    if blocked == "grant":
        app.allowed_endpoints_json = "[]"
    if blocked == "active_lease":
        job.lease_until = jobs.lease_deadline()
    if blocked == "failed":
        job.status = "failed"
    if blocked == "legacy":
        job.generation_json = None
    if blocked == "snapshot":
        job.generation_json = '{"model_snapshot":{"name":"legacy"},"voice":"Kore"}'
    await db.commit()
    monkeypatch.setattr(maintenance, "AsyncSessionLocal", lambda: db)
    claims = await maintenance.recover_expired()
    assert len(claims) == (0 if blocked else 1)
    if not blocked:
        assert claims[0][0] == "job" and claims[0][1] != "first"
        assert await maintenance.recover_expired() == []
        assert await jobs.claim_retry(db, "job", 1) is None


async def test_orphan_sweep_preserves_retry_checkpoints_active_uploads_and_final_audio(
    context, monkeypatch  # noqa: F811
):  # noqa: F811
    db, _, _, _ = context
    await seed(db)
    row = (await db.execute(select(TTSChunk).where(TTSChunk.chunk_index == 0))).scalar_one()
    row.object_key = "tts_audio/chunks/job/old/0000.wav"
    await db.commit()
    monkeypatch.setattr(maintenance, "AsyncSessionLocal", lambda: db)
    old = jobs._now() - datetime.timedelta(days=2)
    recent = jobs._now()
    objects = [
        {"Key": row.object_key, "LastModified": old},
        {"Key": "tts_audio/chunks/job/first/0001.wav", "LastModified": old},
        {"Key": "tts_audio/chunks/absent/old/0000.wav", "LastModified": old},
        {"Key": "tts_audio/chunks/absent/new/0001.wav", "LastModified": recent},
        {"Key": "tts_audio/tts_final.wav", "LastModified": old},
    ]
    deleted = []

    class Storage:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def list_objects_v2(self, **kwargs):
            return {
                "Contents": objects[:2] if not kwargs.get("ContinuationToken") else objects[2:],
                "IsTruncated": not kwargs.get("ContinuationToken"),
                "NextContinuationToken": "page-2",
            }

        async def delete_object(self, Key, **kwargs):
            deleted.append(Key)

    monkeypatch.setattr(
        maintenance,
        "get_b2_session",
        lambda: (SimpleNamespace(client=lambda *a, **k: Storage()), None),
    )
    assert await maintenance.cleanup_orphans() == 1
    assert deleted == ["tts_audio/chunks/absent/old/0000.wav"]


async def test_50000_character_job_has_bounded_concurrency_and_ordered_final(context, monkeypatch):  # noqa: F811
    db, storage, _, _ = context
    text = "word " * 10000
    chunks = split_text_into_chunks(text, 2500)
    job = await seed(db, count=len(chunks))
    job.text = text
    rows = (await db.execute(select(TTSChunk).order_by(TTSChunk.chunk_index))).scalars().all()
    for row, chunk in zip(rows, chunks, strict=True):
        row.text = chunk
    await db.commit()
    active = peak = 0
    calls = 0

    async def generate(**kwargs):
        nonlocal active, peak, calls
        index = calls
        calls += 1
        assert kwargs['text'] == chunks[index] and len(kwargs['text']) <= 2500
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        return pcm_to_wav(bytes([index + 1, 0]) * 24000), "audio/wav"

    monkeypatch.setattr(jobs, "generate_dynamic_speech", generate)
    await run(job)
    await db.refresh(job)
    assert len(chunks) == 20 and peak == 2 and job.status == "completed"
    assert get_wav_stats(storage.objects[job.object_key])["frame_count"] == 20 * 24000
    assert storage.objects[job.object_key][44:] == b''.join(bytes([i + 1, 0]) * 24000 for i in range(20))
    assert len(storage.deleted) == 20 and len(storage.objects) == 1


async def test_maintenance_shutdown_cancels_recovered_workers(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    recover = AsyncMock(return_value=[('job', 'token')])
    async def worker(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    monkeypatch.setattr(maintenance, 'recover_expired', recover)
    monkeypatch.setattr(maintenance, 'process_tts_job', worker)
    monkeypatch.setattr(maintenance, 'cleanup_orphans', AsyncMock())
    task = asyncio.create_task(maintenance.maintenance_loop())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


async def test_interrupted_worker_resumes_without_regenerating_completed_prefix(context, monkeypatch):  # noqa: F811
    db, storage, _, _ = context
    owner = User(google_id='recovery-owner', email='recovery@example.test', is_active=True, api_access_enabled=True)
    db.add(owner)
    await db.flush()
    db.add(AuthorizedApp(id=1, name='Recovery app', owner_user_id=owner.id,
        allowed_origin='https://app.test', is_active=True, is_approved=True,
        allowed_endpoints_json=json.dumps(['POST /api/v1/audio/tts'])))
    await db.commit()
    job = await seed(db)
    blocked = asyncio.Event()
    async def interrupted(**kwargs):
        index = int(kwargs['text'])
        if index:
            blocked.set()
            await asyncio.Future()
        return wav(index), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', interrupted)
    worker = asyncio.create_task(run(job))
    await asyncio.wait_for(blocked.wait(), timeout=2)
    first = (await db.execute(select(TTSChunk).where(TTSChunk.chunk_index == 0))).scalar_one()
    assert first.status == 'completed'
    original_key = first.object_key
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert job.status == 'processing' and original_key in storage.objects
    job.lease_until = jobs._now() - datetime.timedelta(seconds=1)
    await db.commit()
    monkeypatch.setattr(maintenance, 'AsyncSessionLocal', lambda: db)
    recovered = await maintenance.recover_expired()
    calls = []
    async def resumed(**kwargs):
        index = int(kwargs['text'])
        calls.append(index)
        return wav(index), 'audio/wav'
    monkeypatch.setattr(jobs, 'generate_dynamic_speech', resumed)
    await jobs.process_tts_job(*recovered[0][:1], run_token=recovered[0][1])
    await db.refresh(job)
    assert calls == [1, 2] and job.status == 'completed'
    assert len(storage.deleted) == 3 and len(storage.objects) == 1


async def test_lifespan_starts_and_stops_background_services_without_live_calls(monkeypatch):
    import main
    started, stopped = [], []
    async def service(name):
        started.append(name)
        try:
            await asyncio.Future()
        finally:
            stopped.append(name)
    upload = AsyncMock()
    monkeypatch.setattr(main, 'archive_log_loop', lambda: service('logs'))
    monkeypatch.setattr(main, 'maintenance_loop', lambda: service('tts'))
    monkeypatch.setattr(main, 'upload_log_batches', upload)
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert sorted(started) == ['logs', 'tts']
    assert sorted(stopped) == ['logs', 'tts']
    upload.assert_awaited_once()
