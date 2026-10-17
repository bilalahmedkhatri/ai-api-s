"""Unavailable models cannot create jobs, serve samples or restart old jobs."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from app.api.v1.audio import router as routes
from app.core.auth import get_current_api_key
from app.models.db_models import TTSChunk, TTSJob, TTSModel
from app.services.tts import catalog, generator
from fastapi import BackgroundTasks, HTTPException
from main import app
from sqlalchemy import func, select


@pytest.fixture
def authenticated(monkeypatch):
    async def auth():
        return SimpleNamespace(authorized_app_id=1)
    monkeypatch.setitem(app.dependency_overrides, get_current_api_key, auth)


async def test_catalog_hides_any_local_kokoro_name(unit_db):
    for name, provider, local in [('local-custom', 'KoKoRo', True),
                                  ('remote', 'kokoro', False), ('gemini', 'gemini', False)]:
        unit_db.add(TTSModel(name=name, provider=provider, is_local=local, is_active=True))
    await unit_db.commit()
    assert {m['name'] for m in await catalog.get_dynamic_tts_models(unit_db)} == {'remote', 'gemini'}
    with pytest.raises(ValueError):
        await catalog.get_dynamic_voices('local-custom', unit_db)


async def test_local_request_cannot_create_jobs(unit_db, authenticated):
    unit_db.add(TTSModel(name='local-custom', provider='kokoro', is_local=True, is_active=True))
    await unit_db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/audio/tts', json={
            'model': 'local-custom', 'text': 'Hello', 'voice': 'af_sarah'})
    assert response.status_code == 400
    for table in (TTSJob, TTSChunk):
        assert (await unit_db.execute(select(func.count()).select_from(table))).scalar_one() == 0


async def test_local_sample_rejected_before_cache(monkeypatch):
    monkeypatch.setattr(generator, '_load_model', AsyncMock(return_value=SimpleNamespace(
        provider='kokoro', is_local=True, default_voice='af_sarah')))
    cache = Mock(return_value=(b'cached', 'audio/wav'))
    monkeypatch.setattr(generator, 'get_cached_voice_sample', cache)
    with pytest.raises(ValueError, match='not available'):
        await generator.generate_dynamic_speech('local-custom', 'Hello', is_sample_request=True)
    cache.assert_not_called()


async def test_remote_kokoro_still_dispatches(monkeypatch):
    monkeypatch.setattr(generator, '_load_model', AsyncMock(return_value=SimpleNamespace(
        provider='kokoro', is_local=False, default_voice='af_sarah', default_speed=1.0,
        default_lang='en-us', provider_config={}, api_endpoint_url=None)))
    remote = AsyncMock(return_value=(b'audio', 'audio/wav'))
    monkeypatch.setattr(generator, '_generate_kokoro_live', remote)
    assert await generator.generate_dynamic_speech('remote', 'Hello') == (b'audio', 'audio/wav')
    remote.assert_awaited_once()


async def test_local_retry_does_not_claim_or_schedule(monkeypatch):
    from app.services.tts import jobs
    job = SimpleNamespace(status='failed', generation_json=json.dumps({
        'model_snapshot': {'provider': 'kokoro', 'is_local': True}}))
    result = SimpleNamespace(scalar_one_or_none=lambda: job)
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    claim = AsyncMock()
    monkeypatch.setattr(jobs, 'claim_retry', claim)
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        await routes.retry_tts_endpoint('old-local-job', tasks,
            SimpleNamespace(authorized_app_id=1), db)
    assert error.value.status_code == 400
    claim.assert_not_awaited()
    assert not tasks.tasks
