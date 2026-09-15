import asyncio
from app.db.database import get_db
from app.models.db_models import TTSVoice, TTSModel
from sqlalchemy import select

async def run():
    async for db in get_db():
        m = await db.execute(select(TTSModel).where(TTSModel.name=='gemini-2.5-flash-preview-tts'))
        model = m.scalar_one_or_none()
        if not model: return
        stmt = select(TTSVoice).where(TTSVoice.model_id == model.id).limit(10).offset(0)
        r = await db.execute(stmt)
        voices = r.scalars().all()
        print([v.voice_name for v in voices])
        break

if __name__ == "__main__":
    asyncio.run(run())
