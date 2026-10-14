"""UTC JSONL batches with retryable private B2 archival."""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if k.lower() in {'password', 'secret', 'api_key', 'authorization', 'access_token', 'refresh_token'} else _redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if not isinstance(value, str):
        return value
    def clean_url(match):
        try:
            url = urlsplit(match.group())
            return urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path, '', ''))
        except ValueError:
            return '[REDACTED_URL]'
    value = re.sub(r"[a-zA-Z][\w+.-]*://[^\s'\"<>]+", clean_url, value)
    value = re.sub(r'(?i)Bearer\s+[^\s,\"\']+', 'Bearer [REDACTED]', value)
    value = re.sub(r"(?i)((?:password|api[_-]?key|access_token|refresh_token|secret)['\"]?\s*[:=]\s*['\"]?)[^\s,'\"}]+", r'\1[REDACTED]', value)
    return value


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {'ts': datetime.fromtimestamp(record.created, UTC).isoformat(),
                   'level': record.levelname, 'logger': record.name, 'msg': record.getMessage()}
        for key in ('request_id', 'path', 'input_type', 'cache_hit', 'elapsed_ms'):
            if getattr(record, key, None) is not None:
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload['exc'] = self.formatException(record.exc_info)
        return json.dumps(_redact(payload), ensure_ascii=False)


def _stamp(ts):
    return datetime.fromisoformat(ts).astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')


def _batch_name(lines):
    first, last = json.loads(lines[0])['ts'], json.loads(lines[-1])['ts']
    digest = hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest()[:12]
    return f'logs_{_stamp(first)}__{_stamp(last)}_{digest}.jsonl'


class _JsonlBatchHandler(logging.Handler):
    """One worker writes gateway.jsonl; closed batches are atomically renamed."""
    def __init__(self, log_dir='logs', max_entries=1000):
        super().__init__()
        if max_entries < 1:
            raise ValueError('Log batch size must be positive')
        self.directory = Path(log_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ready = self.directory / 'pending'
        self.ready.mkdir(exist_ok=True)
        self.active = self.directory / 'gateway.jsonl'
        self.max_entries = max_entries
        self.count = 0
        self.lines = []
        self.setFormatter(_JsonFormatter())
        # Recover an interrupted/old active file without discarding records.
        # Claim each file before reading it: a running Windows dev server may
        # still hold gateway.jsonl. Never copy/archive that writer's live file.
        for candidate in sorted(self.directory.glob('gateway*.jsonl')):
            recovered_path = candidate.with_suffix('.recovering')
            try:
                candidate.replace(recovered_path)
            except PermissionError:
                if candidate == self.active:
                    self.active = self.directory / f'gateway-{os.getpid()}.jsonl'
        for recovered_path in sorted(self.directory.glob('gateway*.recovering')):
            recovered = []
            fallback_ts = datetime.fromtimestamp(recovered_path.stat().st_mtime, UTC).isoformat()
            for line in recovered_path.read_text(encoding='utf-8').splitlines():
                try:
                    payload = json.loads(line)
                    payload.setdefault('ts', fallback_ts)
                    _stamp(payload['ts'])
                except (ValueError, TypeError, AttributeError):
                    payload = {'ts': fallback_ts, 'level': 'WARNING', 'msg': 'Recovered malformed log record'}
                recovered.append(json.dumps(_redact(payload), ensure_ascii=False))
            for start in range(0, len(recovered), max_entries):
                batch = recovered[start:start + max_entries]
                target = self.ready / _batch_name(batch)
                temp = target.with_suffix('.tmp')
                temp.write_text('\n'.join(batch) + '\n', encoding='utf-8')
                temp.replace(target)
            recovered_path.unlink()
        self.stream = self.active.open('a', encoding='utf-8')

    def emit(self, record):
        try:
            line = self.format(record)
            self.stream.write(line + '\n')
            self.stream.flush()
            self.lines.append(line)
            self.count += 1
            if self.count >= self.max_entries:
                self._seal()
        except Exception:
            self.handleError(record)

    def _seal(self):
        if not self.lines:
            return
        self.stream.close()
        self.active.replace(self.ready / _batch_name(self.lines))
        self.stream = self.active.open('a', encoding='utf-8')
        self.lines, self.count = [], 0

    def seal(self):
        with self.lock:
            self._seal()

    def close(self):
        with self.lock:
            if hasattr(self, 'stream'):
                self._seal()
                self.stream.close()
        super().close()


async def upload_log_batches(log_dir='logs'):
    # Lazy imports keep logging setup independent of DB/provider initialization.
    from app.core.config import settings
    from app.services.media_extractor import get_b2_session
    files = sorted((Path(log_dir) / 'pending').glob('logs_*.jsonl'))
    if not files:
        return
    try:
        session, config = get_b2_session()
        async with session.client('s3', endpoint_url=settings.b2_endpoint_url, config=config) as s3:
            for path in files:
                data = await asyncio.to_thread(path.read_bytes)
                # Same key on every attempt makes timeout/restart retries idempotent.
                key = f'logs/{path.name[5:9]}/{path.name[9:11]}/{path.name}'
                await s3.put_object(Bucket=settings.b2_bucket_name, Key=key, Body=data,
                                    ContentType='application/x-ndjson')
                path.unlink()  # Delete only after confirmed upload.
    except Exception as exc:
        logging.getLogger(__name__).warning('B2 log archival deferred error_type=%s; local batches retained', type(exc).__name__)


async def archive_log_loop(log_dir='logs'):
    while True:
        await upload_log_batches(log_dir)
        await asyncio.sleep(5)


def setup_logging(log_dir='logs', max_entries=1000):
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[_JsonlBatchHandler(log_dir, max_entries), console])
    logging.getLogger('phonemizer').setLevel(logging.ERROR)
