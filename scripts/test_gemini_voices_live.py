import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Zephyr", "Ursa", "Orion", "Pega"]
MODELS = ["gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview"]

async def test_voice(model: str, voice: str):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": f"Testing voice {voice} on model {model}."}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice
                    }
                }
            }
        }
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if "candidates" in data and len(data["candidates"]) > 0:
                    print(f"SUCCESS: Model '{model}' with Voice '{voice}' -> HTTP 200 OK")
                else:
                    print(f"FAILED (No Candidates): Model '{model}' with Voice '{voice}' -> Response: {data}")
            else:
                print(f"FAILED (HTTP {resp.status_code}): Model '{model}' with Voice '{voice}' -> Error: {resp.text[:150]}")
        except Exception as e:
            print(f"ERROR: Model '{model}' with Voice '{voice}' -> Exception: {e}")

async def main():
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not found in environment!")
        return
    print(f"Testing {len(VOICES)} voices across {len(MODELS)} models...\n")
    for m in MODELS:
        print(f"--- Testing Model: {m} ---")
        for v in VOICES:
            await test_voice(m, v)
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
