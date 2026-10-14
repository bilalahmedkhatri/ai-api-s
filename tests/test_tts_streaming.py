"""Ordered streaming, late reads and tokenizer regressions; no live providers."""
import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from app.services.tts import gemini_provider, streaming, text_utils
from main import app

from tests.test_tts_chunks import context, run, seed, wav  # noqa: F401


@pytest.mark.parametrize('provider', ['gemini', 'kokoro'])
async def test_completed_audio_reconstructs_deleted_chunks(context, monkeypatch, provider):  # noqa: F811
    db, storage, _, _ = context
    monkeypatch.setattr(streaming, 'AsyncSessionLocal', lambda: db)
    job = await seed(db, provider=provider)
    async def generate(**kwargs):
        assert kwargs['voice'] == 'Kore'
        return wav(int(kwargs['text'])), 'audio/wav'
    monkeypatch.setattr('app.services.tts.jobs.generate_dynamic_speech', generate)
    await run(job)
    assert len(storage.deleted) == 3 and len(storage.objects) == 1
    events = [json.loads(line) async for line in streaming.stream_chunks('job', 1, after=1)]
    assert [e['index'] for e in events[:-1]] == [1, 2]
    for event in events[:-1]:
        assert base64.b64decode(event['audio']) == wav(event['index'])
    assert events[-1] == {'event': 'completed', 'next_index': 3}
    assert [json.loads(e)['event'] async for e in streaming.stream_chunks('job', 2)] == ['error']


async def test_later_ready_chunk_waits_for_missing_prefix(monkeypatch):
    job = {'status': 'processing', 'lease_until': None}
    chunks = [{'status': 'pending', 'key': '0'}, {'status': 'completed', 'key': '1'}]
    monkeypatch.setattr(streaming, 'snapshot', AsyncMock(return_value=(job, chunks)))
    read = AsyncMock(side_effect=lambda key: wav(int(key)))
    monkeypatch.setattr(streaming, '_get', read)
    async def next_snapshot(_):
        read.assert_not_awaited()
        chunks[0]['status'] = 'completed'
        job['status'] = 'completed'
    monkeypatch.setattr(streaming.asyncio, 'sleep', next_snapshot)
    events = [json.loads(line) async for line in streaming.stream_chunks('job', 1)]
    assert [e['index'] for e in events[:-1]] == [0, 1]


async def test_failed_gap_never_emits_later_chunk(monkeypatch):
    monkeypatch.setattr(streaming, 'snapshot', AsyncMock(return_value=(
        {'status': 'failed', 'error': 'Retry missing part'},
        [{'status': 'completed', 'key': '0'}, {'status': 'failed'}, {'status': 'completed', 'key': '2'}])))
    read = AsyncMock(return_value=wav(0))
    monkeypatch.setattr(streaming, '_get', read)
    events = [json.loads(line) async for line in streaming.stream_chunks('job', 1)]
    assert [e['event'] for e in events] == ['chunk', 'failed']
    assert events[-1]['next_index'] == 1
    read.assert_awaited_once_with('0')


async def test_ready_prefix_streams_before_job_finishes(monkeypatch):
    monkeypatch.setattr(streaming, 'snapshot', AsyncMock(return_value=(
        {'status': 'processing', 'lease_until': None},
        [{'status': 'completed', 'key': '0'}, {'status': 'pending'}])))
    monkeypatch.setattr(streaming, '_get', AsyncMock(return_value=wav(0)))
    stream = streaming.stream_chunks('job', 1)
    event = json.loads(await anext(stream))
    assert event['event'] == 'chunk' and event['index'] == 0
    await stream.aclose()


async def test_stream_route_checks_app_scope_and_cursor(context, monkeypatch):  # noqa: F811
    db, _, _, auth = context
    job = await seed(db)
    job.status = 'failed'
    await db.commit()
    monkeypatch.setattr(streaming, 'AsyncSessionLocal', lambda: db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway.test') as client:
        auth.authorized_app_id = 2
        assert (await client.get('/api/v1/audio/tts/stream/job')).status_code == 404
        auth.authorized_app_id = 1
        assert (await client.get('/api/v1/audio/tts/stream/job?after=-1')).status_code == 422
        response = await client.get('/api/v1/audio/tts/stream/job')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-store'
        assert json.loads(response.text)['event'] == 'failed'


def test_punkt_and_unicode_preserve_text_and_limits(monkeypatch):
    assert text_utils._punkt() is not None
    assert text_utils._sentences('Dr. Smith speaks. Next sentence.') == ['Dr. Smith speaks.', 'Next sentence.']
    script = ('Hello world! Urdu here۔ Next text। 中文。再见！ ' * 100) + 'x' * 2600
    chunks = text_utils.split_text_into_chunks(script, 2500)
    assert all(0 < len(c) <= 2500 for c in chunks)
    assert ''.join(''.join(chunks).split()) == ''.join(script.split())
    monkeypatch.setattr(text_utils, '_punkt', lambda: None)
    assert text_utils._sentences('One. Two۔ Three。四！') == ['One.', 'Two۔', 'Three。', '四！', '']


async def test_gemini_invalid_voice_never_silently_changes_voice(monkeypatch):
    requests = []
    async def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(gemini_provider.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(gemini_provider, '_pace_request', AsyncMock())
    with pytest.raises(RuntimeError, match='HTTP=400'):
        await gemini_provider._generate_gemini_speech('Hello', 'Kore', 'https://provider.test', {}, 'fake-key')
    assert len(requests) == 1
    assert requests[0]['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'] == 'Kore'


async def test_three_retries_allow_four_total_attempts(monkeypatch):
    calls = 0
    async def respond(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(gemini_provider.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(gemini_provider, '_pace_request', AsyncMock())
    monkeypatch.setattr(gemini_provider.asyncio, 'sleep', AsyncMock())
    with pytest.raises(RuntimeError, match='after 4 attempts'):
        await gemini_provider._generate_gemini_speech('Hello', 'Kore', 'https://provider.test', {}, 'fake-key')
    assert calls == 4
