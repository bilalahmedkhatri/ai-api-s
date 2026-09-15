import asyncio
import io
from fastapi.testclient import TestClient
from main import app
from app.db.database import get_db

def test_kokoro_tts():
    client = TestClient(app)
    
    payload = {
        "text": "Hello, this is a test audio generation using the Kokoro local model.",
        "model": "kokoro-v1.0-local",
        "voice": "af_bella"
    }
    
    print("Sending POST request to /api/v1/audio/tts with payload:", payload)
    
    response = client.post("/api/v1/audio/tts", json=payload)
    
    print("Status Code:", response.status_code)
    
    if response.status_code == 200:
        print("Success! Audio generated.")
        print("Content-Type:", response.headers.get("content-type"))
        print("Audio length (bytes):", len(response.content))
    else:
        print("Error Response:")
        try:
            print(response.json())
        except:
            print(response.text)

if __name__ == "__main__":
    test_kokoro_tts()
