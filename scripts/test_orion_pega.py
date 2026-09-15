import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

async def test_single_voice(voice: str):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": f"Hello! This is a test of {voice} voice."}]}],
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
        resp = await client.post(url, json=payload)
        print(f"Voice '{voice}': Status Code = {resp.status_code}")
        if resp.status_code != 200:
            print(f"Error Response: {resp.text}")

async def main():
    print("Testing Orion...")
    await test_single_voice("Orion")
    print("Waiting 5 seconds to avoid RPM limit...")
    await asyncio.sleep(5)
    print("Testing Pega...")
    await test_single_voice("Pega")

if __name__ == "__main__":
    asyncio.run(main())
