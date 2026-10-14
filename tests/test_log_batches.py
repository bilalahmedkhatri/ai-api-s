"""Log recovery, redaction and retryable archival with fake storage."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.logging_config import _JsonlBatchHandler, upload_log_batches


def record(message, timestamp=1728000000):
    item = logging.LogRecord('test', logging.INFO, '', 0, message, (), None)
    item.created = timestamp
    return item


def test_thousand_records_rename_with_date_range_and_redact(tmp_path):
    handler = _JsonlBatchHandler(tmp_path)
    for index in range(999):
        handler.handle(record('password=private https://user:pass@example.test/a?key=private', 1728000000 + index))
    assert not list((tmp_path / 'pending').glob('*.jsonl'))
    handler.handle(record('Finished', 1728001000))
    files = list((tmp_path / 'pending').glob('*.jsonl'))
    assert len(files) == 1
    assert files[0].name.startswith('logs_20241004T000000000000Z__20241004T001640000000Z_')
    lines = files[0].read_text().splitlines()
    assert len(lines) == 1000 and 'private' not in lines[0]
    assert 'https://example.test/a' in lines[0]
    handler.close()


@pytest.mark.parametrize('filename', ['gateway.jsonl', 'gateway-123.jsonl', 'gateway.recovering'])
def test_restart_recovers_partial_and_malformed_records(tmp_path, filename):
    (tmp_path / filename).write_text(json.dumps({'ts': '2024-10-04T00:00:00+00:00', 'password': 'secret'}) + '\nmalformed\n')
    handler = _JsonlBatchHandler(tmp_path)
    files = list((tmp_path / 'pending').glob('*.jsonl'))
    assert len(files) == 1 and len(files[0].read_text().splitlines()) == 2
    assert 'secret' not in files[0].read_text()
    assert (tmp_path / 'gateway.jsonl').read_text() == ''
    handler.close()


def test_locked_live_log_is_never_archived_or_modified(tmp_path, monkeypatch):
    active = tmp_path / 'gateway.jsonl'
    active.write_text('A different process owns this log')
    replace = Path.replace
    def locked(path, target):
        if path == active:
            raise PermissionError('File is still open')
        return replace(path, target)
    monkeypatch.setattr(Path, 'replace', locked)
    handler = _JsonlBatchHandler(tmp_path, 1)
    assert handler.active != active
    assert not list((tmp_path / 'pending').glob('*.jsonl'))
    handler.handle(record('New process log'))
    handler.close()
    assert active.read_text() == 'A different process owns this log'
    assert len(list((tmp_path / 'pending').glob('*.jsonl'))) == 1


async def test_failed_upload_keeps_batch_and_retry_uses_same_key(tmp_path, monkeypatch):
    handler = _JsonlBatchHandler(tmp_path, 1)
    handler.handle(record('Hello'))
    handler.close()
    put = AsyncMock(side_effect=RuntimeError('Storage unavailable'))
    class Storage:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        put_object = put
    monkeypatch.setattr('app.services.media_extractor.get_b2_session', lambda: (SimpleNamespace(client=lambda *a, **k: Storage()), None))
    await upload_log_batches(tmp_path)
    assert len(list((tmp_path / 'pending').glob('*.jsonl'))) == 1
    first_key = put.call_args.kwargs['Key']
    assert first_key.startswith('logs/2024/10/logs_')
    put.side_effect = None
    await upload_log_batches(tmp_path)
    assert put.call_args.kwargs['Key'] == first_key
    assert not list((tmp_path / 'pending').glob('*.jsonl'))
