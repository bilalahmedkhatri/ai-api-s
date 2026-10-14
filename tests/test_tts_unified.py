"""Unified TTS regressions: isolated SQL, fake providers/storage, no live requests."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.auth import get_current_api_key
from app.models.db_models import TTSModel
from app.services import tts_service
from app.services.tts import gemini_provider, generator, jobs
from app.services.tts import tts_service as compatibility_service
from main import app


@pytest.mark.parametrize('flow', ['sample', 'job'])
async def test_routes_dispatch_to_same_provider(flow, unit_db, monkeypatch):
    model = TTSModel(name='unified-test', provider='gemini', is_active=True,
                     is_local=False, api_endpoint_url='https://provider.test/tts',
                     default_voice='Kore', provider_config={})
    unit_db.add(model)
    await unit_db.commit()
    monkeypatch.setattr(generator, 'AsyncSessionLocal', lambda: unit_db)
    monkeypatch.setattr(jobs, 'AsyncSessionLocal', lambda: unit_db)
    wav = tts_service.pcm_to_wav(b'\x00\x00' * 240)
    synthesize = AsyncMock(return_value=(wav, 'audio/wav'))
    monkeypatch.setattr(generator, '_generate_gemini_speech', synthesize)
    monkeypatch.setattr(generator, 'get_cached_voice_sample', lambda *args: None)
    monkeypatch.setattr(generator, 'cache_voice_sample', lambda *args: None)
    notify = AsyncMock()
    monkeypatch.setattr(jobs, 'notify_frontend', notify)
    upload = AsyncMock()

    class Storage:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        put_object = upload
        delete_object = AsyncMock()

        async def get_object(self, **kwargs):
            class Body:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
                async def read(self):
                    return wav
            return {"Body": Body()}

    monkeypatch.setattr(jobs, 'get_b2_session', lambda: (SimpleNamespace(client=lambda *a, **k: Storage()), None))
    monkeypatch.setattr(jobs, 'generate_audio_presigned_url', AsyncMock(return_value='https://storage.test/audio.wav'))
    monkeypatch.setattr('app.services.voice_url_service.ensure_valid_audio_url', AsyncMock(return_value='https://storage.test/audio.wav'))
    # A plain dependency function preserves FastAPI's dependency signature.
    async def auth():
        return SimpleNamespace(id=1, authorized_app_id=1)

    app.dependency_overrides[get_current_api_key] = auth
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway.test') as client:
        if flow == 'sample':
            response = await client.get('/api/v1/audio/sample', params={'model': model.name, 'voice': 'Kore', 'text': 'Hello.'})
            assert response.status_code == 200 and response.content == wav
            upload.assert_not_awaited()
        else:
            response = await client.post('/api/v1/audio/tts', json={'model': model.name, 'voice': 'Kore', 'text': 'Hello.'})
            assert response.status_code == 200
            status = await client.get('/api/v1/audio/tts/status/' + response.json()['job_id'])
            assert status.json()['status'] == 'completed'
            assert upload.await_count == 2
            event = notify.call_args.args[0]
            assert event['event'] == 'tts.job.completed' and event['size_bytes'] == len(wav)
    synthesize.assert_awaited_once()
    assert synthesize.call_args.kwargs['text'] == 'Hello.'


@pytest.mark.parametrize('facade', [tts_service, compatibility_service])
async def test_gemini_chunks_retry_5xx_with_header_credentials(monkeypatch, facade):
    requests = []
    async def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'inlineData': {
            'data': base64.b64encode(b'\x00\x00' * 10).decode(), 'mimeType': 'audio/pcm;rate=24000',
        }}]}}]})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(gemini_provider.httpx, 'AsyncClient', lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(gemini_provider, '_resolve_key', AsyncMock(return_value=gemini_provider._ActiveKey('isolated-fake-key')))
    monkeypatch.setattr(gemini_provider, '_mark_used', AsyncMock())
    monkeypatch.setattr(gemini_provider.asyncio, 'sleep', AsyncMock())
    wav, mime = await facade._generate_gemini_speech('word ' * 1100, 'Kore', 'https://provider.test/tts', {}, None)
    assert len(requests) == 4  # three chunks, first chunk retried once
    assert mime == 'audio/wav' and wav.startswith(b'RIFF')
    assert all(r.headers['x-goog-api-key'] == 'isolated-fake-key' and 'key=' not in str(r.url) for r in requests)


async def test_long_script_has_no_new_generator_limit(monkeypatch):
    monkeypatch.setattr(generator, '_load_model', AsyncMock(return_value=SimpleNamespace(
        default_voice='Kore', provider='gemini', api_endpoint_url='https://provider.test/tts', provider_config={})))
    provider = AsyncMock(return_value=(b'audio', 'audio/wav'))
    monkeypatch.setattr(generator, '_generate_gemini_speech', provider)
    await tts_service.generate_dynamic_speech('test', 'a' * 25000)
    assert len(provider.call_args.kwargs['text']) == 25000
