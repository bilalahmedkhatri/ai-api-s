"""app/services/tts/catalog.py — DB queries for TTS models and voices."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import TTSModel, TTSVoice
from app.services.model_endpoints import legacy_views
from app.services.tts.availability import is_local_kokoro


async def get_dynamic_tts_models(db: AsyncSession) -> list[dict]:
    """Fetch all active TTS models from the database."""
    result = await db.execute(select(TTSModel))
    return [
        {"name": m.name, "provider": m.provider, "is_local": m.is_local}
        for m in await legacy_views(db, result.scalars().all(), "tts") if not is_local_kokoro(m.provider, m.is_local)
    ]


async def get_dynamic_voices(
    model_name: str,
    db: AsyncSession,
    search: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Fetch paginated and searchable voices associated with a specific TTS model."""
    model_result = await db.execute(
        select(TTSModel).where(TTSModel.name == model_name)
    )
    model = model_result.scalar_one_or_none()
    models = await legacy_views(db, [model] if model else [], "tts")
    model = models[0] if models else None
    if not model or is_local_kokoro(model.provider, model.is_local):
        raise ValueError(f"Model {model_name} not found or inactive.")

    stmt = select(TTSVoice).where(TTSVoice.model_id == model.id)

    if search and search.strip():
        # escape LIKE wildcards so "%" / "_" in user input are literal
        esc = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{esc}%"
        stmt = stmt.where(
            TTSVoice.voice_name.ilike(pattern, escape="\\")
            | TTSVoice.gender.ilike(pattern, escape="\\")
        )

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total_count = count_result.scalar_one()

    # stable ordering → pagination never repeats / skips rows
    stmt = (
        stmt.order_by(TTSVoice.voice_name, TTSVoice.id)
        .limit(min(max(limit, 1), 100))
        .offset(max(offset, 0))
    )
    voices = (await db.execute(stmt)).scalars().all()

    mapped = [
        {
            "name": v.voice_name,
            "gender": v.gender,
            "sample_text": v.sample_text,
            "sample_audio_url": f"/api/v1/audio/sample?model={model_name}&voice={v.voice_name}",
        }
        for v in voices
    ]
    return mapped, total_count
