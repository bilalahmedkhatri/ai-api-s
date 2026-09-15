import os
import sys
import json
import base64
import urllib.request

# Add project root to sys.path to import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.config import settings

api_key = settings.gemini_api_key

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}"

def synthesize(text, system_instruction=None, filename="out.wav"):
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": "Achernar"
                    }
                }
            }
        }
    }
    
    if system_instruction:
        payload["system_instruction"] = {
            "parts": [{"text": system_instruction}]
        }
        
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            part = data["candidates"][0]["content"]["parts"][0]
            if "inlineData" in part:
                b64 = part["inlineData"]["data"]
            elif "inline_data" in part:
                b64 = part["inline_data"]["data"]
            else:
                print("No audio returned")
                return
            
            audio_bytes = base64.b64decode(b64)
            with open(filename, "wb") as f:
                f.write(audio_bytes)
            print(f"Saved {filename}")
    except Exception as e:
        print(f"Error: {e}")
        if hasattr(e, 'read'):
            print(e.read().decode())

text = "[informative] Welcome to Module Three. [instruction] In this section, we'll cover advanced security."

# 1. Without instruction
synthesize(text, filename="no_instruction.wav")

# 2. With instruction (Audio Profile, Style, Pace, Accent) embedded in text
instruction = "Audio Profile: A clear and authoritative corporate trainer.\nDirector's Note:\nStyle: Newscaster\nPace: Staccato\nAccent: American\n\n"
synthesize(instruction + text, filename="with_instruction.wav")
