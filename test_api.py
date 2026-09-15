import asyncio
import httpx

async def test():
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://localhost:8000/api/v1/audio/voices?model=gemini-2.5-flash-preview-tts")
        print("Status:", resp.status_code)
        if resp.status_code != 200:
            print("Response:", resp.text)

if __name__ == "__main__":
    asyncio.run(test())
