"""Structured JSON logging with rotating file output.

Uses only stdlib: logging, logging.handlers, json, uuid — no third-party log library.
"""

import json
import logging
import logging.handlers
import traceback
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            # Request-scoped fields injected by RequestIDMiddleware
            "request_id": getattr(record, "request_id", None),
            "path": getattr(record, "path", None),
            "input_type": getattr(record, "input_type", None),
            "cache_hit": getattr(record, "cache_hit", None),
            "elapsed_ms": getattr(record, "elapsed_ms", None),
        }
        # Drop None values to keep logs compact
        payload = {k: v for k, v in payload.items() if v is not None}

        if record.exc_info:
            payload["exc"] = traceback.format_exception(*record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_dir: str = "logs", max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5) -> None:
    """Configure root logger: JSON rotating file + plain stderr."""
    Path(log_dir).mkdir(exist_ok=True)

    json_handler = logging.handlers.RotatingFileHandler(
        filename=f"{log_dir}/gateway.jsonl",
        maxBytes=max_bytes,       # 10 MB per file
        backupCount=backup_count, # keep 5 old files
        encoding="utf-8",
    )
    json_handler.setFormatter(_JsonFormatter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    logging.basicConfig(level=logging.INFO, handlers=[json_handler, console_handler])

    # Silence verbose internal token-mismatch logger from phonemizer
    logging.getLogger("phonemizer").setLevel(logging.ERROR)

