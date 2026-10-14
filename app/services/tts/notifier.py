"""app/services/tts/notifier.py — push job result + metadata to the frontend server.

Settings (all optional, read with getattr so nothing breaks if missing):
    frontend_webhook_url     e.g. https://myapp.com/api/tts/webhook
    frontend_webhook_secret  shared secret for HMAC-SHA256 signature
"""

import asyncio
import hashlib
import hmac
import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_WEBHOOK_RETRIES = 3


async def notify_frontend(payload: dict, url: str | None = None) -> bool:
    """POST payload to the frontend server. NEVER raises — a webhook failure must not fail the job."""
    target = url or getattr(settings, "frontend_webhook_url", None)
    if not target:
        return False

    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json"}
    secret = getattr(settings, "frontend_webhook_secret", None)
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-TTS-Signature"] = f"sha256={sig}"

    for attempt in range(1, _WEBHOOK_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(target, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("Frontend webhook HTTP %s (attempt %d)", resp.status_code, attempt)
        except Exception as e:
            logger.warning("Frontend webhook error (attempt %d): %s", attempt, e)
        if attempt < _WEBHOOK_RETRIES:
            await asyncio.sleep(2 ** attempt)

    logger.error("Frontend webhook failed for job %s", payload.get("job_id"))
    return False
