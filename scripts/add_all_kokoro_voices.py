import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import engine
from app.models.db_models import TTSModel, TTSVoice
from sqlalchemy import select
from kokoro_onnx import Kokoro

async def add_voices():
    # Get all voices directly from the bin file
    k = Kokoro('kokoro-v1_0.onnx', 'voices-v1_0.bin')
    all_voices = k.get_voices()
    
    async with AsyncSession(engine) as session:
        # Get Kokoro models
        kokoro_local = await session.execute(select(TTSModel).where(TTSModel.name == "kokoro-v1.0-local"))
        kokoro_local = kokoro_local.scalar_one_or_none()
        
        kokoro_live = await session.execute(select(TTSModel).where(TTSModel.name == "kokoro-v1.0-live"))
        kokoro_live = kokoro_live.scalar_one_or_none()

        models = [m for m in (kokoro_local, kokoro_live) if m]
        
        added_count = 0
        for m in models:
            for voice_name in all_voices:
                # Determine gender from name
                gender = "Female" if "f_" in voice_name else "Male"
                
                existing = await session.execute(
                    select(TTSVoice).where(
                        TTSVoice.model_id == m.id, 
                        TTSVoice.voice_name == voice_name
                    )
                )
                if not existing.scalar_one_or_none():
                    session.add(TTSVoice(
                        model_id=m.id,
                        voice_name=voice_name,
                        gender=gender,
                        sample_text=f"This is a sample for {voice_name}."
                    ))
                    added_count += 1
                    
        await session.commit()
        print(f"Successfully added {added_count} new Kokoro voices to the database.")

if __name__ == "__main__":
    asyncio.run(add_voices())
