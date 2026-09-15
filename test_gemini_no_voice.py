import asyncio
import httpx
from app.core.config import settings

async def test_gemini():
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
    key = settings.gemini_api_key
    payload = {
        "contents": [{"parts": [{"text": "Hello, this is a test."}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"]
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            print("Sending request without voiceName...")
            resp = await client.post(f"{url}?key={key}", json=payload, timeout=10)
            print("Status:", resp.status_code)
            print("Response text length:", len(resp.text))
            if resp.status_code != 200:
                print(resp.text)
        except Exception as e:
            print("Failed:", e)

if __name__ == "__main__":
    asyncio.run(test_gemini())
