import asyncio
from app.db.database import get_db
from app.models.db_models import TTSModel
from sqlalchemy import select

async def run():
    async for db in get_db():
        m = await db.execute(select(TTSModel).where(TTSModel.name=='gemini-2.5-flash-preview-tts'))
        model = m.scalar_one_or_none()
        if not model: return
        config = model.provider_config
        print("Config from DB:", config)
        print("Type:", type(config))
        
        voice = "Puck"
        payload = {
            "contents": [{"parts": [{"text": "Hello, this is a test."}]}],
            "generationConfig": config or {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }

        # Ensure the requested voice always overrides whatever is stored in DB config
        gen_cfg = payload["generationConfig"]
        if "speechConfig" in gen_cfg:
            gen_cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] = voice
            
        print("Final payload:", payload)
        break

if __name__ == "__main__":
    asyncio.run(run())
