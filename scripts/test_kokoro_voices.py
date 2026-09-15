import sys
from pathlib import Path
import asyncio

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.api.v1.audio.router import router
from fastapi import FastAPI
from app.db.database import get_db

app = FastAPI()
app.include_router(router, prefix="/api/v1")

client = TestClient(app)

def test_fetch_voices():
    print("Hitting API: /api/v1/audio/voices?model=kokoro-v1.0-local")
    response = client.get("/api/v1/audio/voices?model=kokoro-v1.0-local")
    print(f"Status Code: {response.status_code}")
    print("Response JSON:")
    print(response.json())

if __name__ == "__main__":
    test_fetch_voices()
