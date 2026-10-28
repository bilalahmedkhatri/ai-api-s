"""Durable app/external-user-scoped idempotency for initial TTS creation."""
import hashlib
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text

from app.models.db_models import TTSJob


async def submission_identity(db, request, req, app_id):
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return str(uuid.uuid4()), None, None
    external = request.headers.get("X-External-User-ID", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,128}", key) or not external or len(external) > 128 or any(ord(char) < 32 for char in external):
        raise HTTPException(400, "Invalid submission identity")
    identity = f"tts:{app_id}:{external}:{key}"
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
    fingerprint = hashlib.sha256(json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if db.get_bind().dialect.name == "postgresql":
        # Held until the existing job-creation transaction commits. Contending
        # requests re-read after acquiring it, before any background task starts.
        lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    existing = (await db.execute(select(TTSJob).where(
        TTSJob.job_id == job_id, TTSJob.authorized_app_id == app_id,
    ))).scalar_one_or_none()
    if existing:
        snapshot = json.loads(existing.generation_json or "{}")
        if snapshot.get("submission_fingerprint") != fingerprint:
            raise HTTPException(409, "Submission key was already used for different input")
    return job_id, fingerprint, existing
