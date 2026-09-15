import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import engine
from app.models.db_models import TTSModel, TTSVoice
from sqlalchemy import select

GEMINI_VOICES = [
    {"name": "Puck",      "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Puck."},
    {"name": "Charon",    "gender": "Male",   "accent": "American", "sample_text": "Welcome. I am Charon."},
    {"name": "Kore",      "gender": "Female", "accent": "American", "sample_text": "Hi there, I am Kore."},
    {"name": "Fenrir",    "gender": "Male",   "accent": "American", "sample_text": "Greetings, I am Fenrir."},
    {"name": "Aoede",     "gender": "Female", "accent": "American", "sample_text": "Hi there! I am Aoede."},
    {"name": "Leda",      "gender": "Female", "accent": "American", "sample_text": "Hello, I am Leda."},
    {"name": "Orus",      "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Orus."},
    {"name": "Zephyr",    "gender": "Female", "accent": "American", "sample_text": "Hello, I am Zephyr."},
    {"name": "Achernar",  "gender": "Female", "accent": "American", "sample_text": "Hello, I am Achernar."},
    {"name": "Despina",   "gender": "Female", "accent": "American", "sample_text": "Hello, I am Despina."},
    {"name": "Erinome",   "gender": "Female", "accent": "American", "sample_text": "Hello, I am Erinome."},
    {"name": "Algieba",   "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Algieba."},
    {"name": "Autonoe",   "gender": "Female", "accent": "American", "sample_text": "Hello, I am Autonoe."},
    {"name": "Callirrhoe","gender": "Female", "accent": "American", "sample_text": "Hello, I am Callirrhoe."},
    {"name": "Vindemiatrix","gender":"Female","accent": "American", "sample_text": "Hello, I am Vindemiatrix."},
    {"name": "Sulafat",   "gender": "Female", "accent": "American", "sample_text": "Hello, I am Sulafat."},
    {"name": "Umbriel",   "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Umbriel."},
    {"name": "Aljanah",   "gender": "Female", "accent": "American", "sample_text": "Hello, I am Aljanah."},
    {"name": "Iapetus",   "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Iapetus."},
    {"name": "Laomedeia", "gender": "Female", "accent": "American", "sample_text": "Hello, I am Laomedeia."},
    {"name": "Pulcherrima","gender":"Female", "accent": "American", "sample_text": "Hello, I am Pulcherrima."},
    {"name": "Rasalgethi","gender": "Male",   "accent": "American", "sample_text": "Hello, I am Rasalgethi."},
    {"name": "Sadachbia", "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Sadachbia."},
    {"name": "Sadaltager","gender": "Male",   "accent": "American", "sample_text": "Hello, I am Sadaltager."},
    {"name": "Schedar",   "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Schedar."},
    {"name": "Sterope",   "gender": "Female", "accent": "American", "sample_text": "Hello, I am Sterope."},
    {"name": "Zubenelgenubi","gender":"Male", "accent": "American", "sample_text": "Hello, I am Zubenelgenubi."},
    {"name": "Enceladus", "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Enceladus."},
    {"name": "Gacrux",    "gender": "Male",   "accent": "American", "sample_text": "Hello, I am Gacrux."},
    {"name": "Achird",    "gender": "Female", "accent": "American", "sample_text": "Hello, I am Achird."},
]

KOKORO_VOICES = [
    {"name": "af_sarah",  "gender": "Female", "sample_text": "Hello, this is Sarah speaking from Kokoro."},
    {"name": "am_adam",   "gender": "Male",   "sample_text": "Hi, I am Adam, a voice from Kokoro."},
    {"name": "af_bella",  "gender": "Female", "sample_text": "Greetings, I am Bella."},
]


async def _upsert_model(session: AsyncSession, name: str, **kwargs) -> TTSModel:
    """Get or create a TTSModel row; update fields on existing rows."""
    result = await session.execute(select(TTSModel).where(TTSModel.name == name))
    model = result.scalar_one_or_none()
    if not model:
        model = TTSModel(name=name, **kwargs)
        session.add(model)
        await session.flush()
        print(f"  + Created model: {name}")
    return model


async def _seed_voices(session: AsyncSession, model: TTSModel, voices: list[dict]) -> None:
    """Insert missing voices for a model; skip duplicates."""
    added = 0
    for v in voices:
        exists = await session.execute(
            select(TTSVoice).where(
                TTSVoice.model_id == model.id,
                TTSVoice.voice_name == v["name"],
            )
        )
        if not exists.scalar_one_or_none():
            session.add(TTSVoice(
                model_id=model.id,
                voice_name=v["name"],
                gender=v.get("gender"),
                accent=v.get("accent"),
                sample_text=v.get("sample_text"),
            ))
            added += 1
    if added:
        print(f"    + Added {added} voice(s) to '{model.name}'")


async def seed_models():
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            await _run_seed()
            return
        except Exception as exc:
            if attempt == max_attempts:
                print(f"All {max_attempts} attempts failed. Last error: {exc}")
                raise
            wait = 3 * (2 ** (attempt - 1))  # 3 s, 6 s
            print(f"Attempt {attempt} failed ({type(exc).__name__}: {exc}). Retrying in {wait} s…")
            await engine.dispose()          # discard dead connections before retry
            await asyncio.sleep(wait)


async def _run_seed():
    async with AsyncSession(engine) as session:
        print("Seeding TTS models…")

        # ── Gemini Flash TTS ──────────────────────────────────────────────
        gemini_flash = await _upsert_model(
            session,
            name="gemini-2.5-flash-preview-tts",
            provider="gemini",
            is_local=False,
            is_active=True,
            api_endpoint_url=(
                "https://generativelanguage.googleapis.com/v1beta/models"
                "/gemini-2.5-flash-preview-tts:generateContent"
            ),
            default_voice="Puck",
            provider_config_json='{"responseModalities": ["AUDIO"]}',
        )
        await _seed_voices(session, gemini_flash, GEMINI_VOICES)

        # ── Gemini Flash Lite TTS (was 404-ing — missing from DB) ─────────
        gemini_lite = await _upsert_model(
            session,
            name="gemini-2.5-flash-lite-tts-preview",
            provider="gemini",
            is_local=False,
            is_active=True,
            api_endpoint_url=(
                "https://generativelanguage.googleapis.com/v1beta/models"
                "/gemini-2.5-flash-lite-tts-preview:generateContent"
            ),
            default_voice="Puck",
            provider_config_json='{"responseModalities": ["AUDIO"]}',
        )
        await _seed_voices(session, gemini_lite, GEMINI_VOICES)

        # ── Kokoro Local ──────────────────────────────────────────────────
        kokoro_local = await _upsert_model(
            session,
            name="kokoro-v1.0-local",
            provider="kokoro",
            is_local=True,
            is_active=True,
            default_voice="af_sarah",
            default_speed=1.0,
            provider_config_json='{"model_path": "kokoro-v1_0.onnx", "voices_path": "voices-v1_0.bin"}',
        )
        await _seed_voices(session, kokoro_local, KOKORO_VOICES)

        # ── Kokoro Live / Replicate ───────────────────────────────────────
        kokoro_live = await _upsert_model(
            session,
            name="kokoro-v1.0-live",
            provider="kokoro",
            is_local=False,
            is_active=True,
            api_endpoint_url="https://api.replicate.com/v1/predictions",
            default_voice="af_sarah",
            provider_config_json='{"replicate_model": "hexgrad/kokoro-tts"}',
        )
        await _seed_voices(session, kokoro_live, KOKORO_VOICES)

        await session.commit()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(seed_models())
