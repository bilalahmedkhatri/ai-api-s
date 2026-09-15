import asyncio
from app.db.database import get_db
from app.models.db_models import TTSModel
from sqlalchemy import select

async def run():
    async for db in get_db():
        m = await db.execute(select(TTSModel).where(TTSModel.name=='gemini-2.5-flash-preview-tts'))
        model = m.scalar_one_or_none()
        if model:
            print("URL:", model.api_endpoint_url)
        else:
            print("Model not found")
        break

if __name__ == "__main__":
    asyncio.run(run())
