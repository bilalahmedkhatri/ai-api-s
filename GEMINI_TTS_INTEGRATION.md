# Gemini 2.0 Flash Text-to-Speech (TTS) API - Integration Guide

> **Target Audience:** Frontend Developers, Mobile App Engineers, AI Agents & Backend Integrators.  
> **Base URL:** `http://localhost:8000` (or production host)  
> **Audio Output Format:** `audio/wav` (24kHz RIFF PCM WAV buffer)  
> **Authentication:** Standard API Key header `x-api-key: <key>` (if enabled) or direct access.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Available Prebuilt Voices](#2-available-prebuilt-voices)
3. [API Endpoints Reference](#3-api-endpoints-reference)
   - [1. List Available Voices](#1-get-apiv1audiogemini-voices)
   - [2. Stream Voice Preview Sample](#2-get-apiv1audiogemini-sample)
   - [3. Synthesize Text to Speech](#3-post-apiv1audiogemini-tts)
4. [Frontend Integration Examples](#4-frontend-integration-examples)
   - [JavaScript / Fetch](#javascript--fetch-example)
   - [React Audio Player Component](#react-component-example)
   - [cURL Command](#curl-example)
   - [Python Client](#python-client-example)
5. [Rate Limits & Guidelines](#5-rate-limits--guidelines)
6. [Error Handling](#6-error-handling)

---

## 1. Overview

The **Gemini TTS API** converts input text into natural, expressive speech audio using Google's official **Gemini TTS** models (`gemini-2.5-flash-preview-tts`, `gemini-3.1-flash-tts-preview`, `gemini-2.5-pro-preview-tts`, `gemini-2.5-flash-lite-tts-preview`).

### Official Supported Models
- **`gemini-3.6-flash`** (Default - Latest optimized version)
- **`gemini-3.1-pro`** (High quality version)
- **`gemini-2.5-flash-preview-tts`** (Legacy fallback): Fast, cost-efficient, real-time speech generation.
- **`gemini-3.1-flash-tts-preview`**: Next-gen low-latency TTS model with advanced audio tags and style control.
- **`gemini-2.5-pro-preview-tts`**: High-quality, long-form professional narrative speech synthesis.
- **`gemini-2.5-flash-lite-tts-preview`**: Lightweight, high-throughput model.

### Key Capabilities
- **High Quality Audio:** 24kHz studio-quality natural audio stream in standard WAV format.
- **9 Controllable Prebuilt Voices:** Expressive male and female voices (`Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede`, `Zephyr`, `Ursa`, `Orion`, `Pega`).
- **Prompt & Style Tags Control:** Support for natural language emotion/tone audio tags in text (e.g. `[excited]`, `[whispering]`, `[speaking slowly]`, `[calm]`).
- **Zero Frontend Audio Decoding Required:** API returns ready-to-play binary `.wav` audio directly consumable by `<audio>` HTML elements or audio players.
- **Live Preview Endpoints:** Dedicated voice discovery and preview sampling endpoints for effortless UI dropdown building.

---

## 2. Available Prebuilt Voices

| Voice Name | Gender | Tone / Style | Sample Preview Link |
| :--- | :--- | :--- | :--- |
| **`Puck`** | Male | Upbeat, energetic, natural | `/api/v1/audio/gemini-sample?voice=Puck` |
| **`Charon`** | Male | Deep, resonant, calm authoritative | `/api/v1/audio/gemini-sample?voice=Charon` |
| **`Kore`** | Female | Firm, clear, professional | `/api/v1/audio/gemini-sample?voice=Kore` |
| **`Fenrir`** | Male | Bold, expressive, confident | `/api/v1/audio/gemini-sample?voice=Fenrir` |
| **`Aoede`** | Female | Warm, melodious, friendly | `/api/v1/audio/gemini-sample?voice=Aoede` |
| **`Zephyr`** | Female | Bright, casual, conversational | `/api/v1/audio/gemini-sample?voice=Zephyr` |
| **`Ursa`** | Female | Soothing, gentle, calm | `/api/v1/audio/gemini-sample?voice=Ursa` |
| **`Orion`** | Male | Soft, thoughtful, articulate | `/api/v1/audio/gemini-sample?voice=Orion` |
| **`Pega`** | Female | Cheerful, lively, articulate | `/api/v1/audio/gemini-sample?voice=Pega` |

---

## 3. API Endpoints Reference

### 1. `GET /api/v1/audio/gemini-voices`

Retrieves the list of all available prebuilt voices along with metadata (gender, accent, description, sample text) and audio preview links.

#### Request:
```http
GET /api/v1/audio/gemini-voices HTTP/1.1
Host: localhost:8000
```

#### Response (`200 OK`):
```json
{
  "status": "success",
  "count": 9,
  "voices": [
    {
      "name": "Puck",
      "gender": "Male",
      "accent": "US English",
      "description": "Upbeat, energetic, and natural male voice",
      "sample_text": "Hello! I am Puck, an energetic and natural voice powered by Gemini 3.6 Flash.",
      "sample_audio_url": "/api/v1/audio/gemini-sample?voice=Puck"
    },
    {
      "name": "Kore",
      "gender": "Female",
      "accent": "US English",
      "description": "Firm, clear, and professional female voice",
      "sample_text": "Hello! I am Kore, a professional and clear voice from Gemini.",
      "sample_audio_url": "/api/v1/audio/gemini-sample?voice=Kore"
    }
  ]
}
```

---

### 2. `GET /api/v1/audio/gemini-sample`

Generates or streams a live audio preview (`.wav`) for a specific voice. Ideal for voice selector UI components where users click a speaker icon to hear a voice sample.

#### Query Parameters:
| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `voice` | `string` | No | `Puck` | Voice identifier (e.g. `Puck`, `Kore`, `Charon`) |
| `text` | `string` | No | *Voice sample text* | Custom test sentence to speak |

#### Request:
```http
GET /api/v1/audio/gemini-sample?voice=Kore HTTP/1.1
Host: localhost:8000
```

#### Response (`200 OK`):
- **Headers:** `Content-Type: audio/wav`, `Content-Disposition: inline; filename=sample_Kore.wav`
- **Body:** Binary WAV audio stream.

---

### 3. `POST /api/v1/audio/gemini-tts`

Synthesizes arbitrary text into natural speech audio using Gemini 3.6 Flash.

#### Request Headers:
```http
Content-Type: application/json
```

#### Request Body Schema:
```json
{
  "text": "Hello, world!",
  "voice": "Puck",
  "model": "gemini-3.6-flash"
}
```

| Field | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `text` | `string` | **Yes** | — | Input text to synthesize into speech (1 to 50,000 chars) |
| `voice` | `string` | No | `"Puck"` | Target voice: `Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede`, `Zephyr`, `Ursa`, `Orion`, `Pega` |
| `model` | `string` | No | `"gemini-2.0-flash"` | Model name |

#### Response (`200 OK`):
- **Headers:** `Content-Type: audio/wav`, `Content-Disposition: attachment; filename=gemini_speech_Puck.wav`
- **Body:** Binary WAV audio file buffer.

---

## 4. Frontend Integration Examples

### JavaScript / Fetch Example

```javascript
// Function to generate and play TTS audio in browser
async function speakText(text, voice = "Puck") {
  try {
    const response = await fetch("http://localhost:8000/api/v1/audio/gemini-tts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text, voice }),
    });

    if (!response.ok) {
      throw new Error(`TTS failed with status ${response.status}`);
    }

    // Convert response to Audio Blob
    const audioBlob = await response.blob();
    const audioUrl = URL.createObjectURL(audioBlob);

    // Play Audio
    const audio = new Audio(audioUrl);
    await audio.play();
  } catch (error) {
    console.error("Audio playback error:", error);
  }
}

// Usage:
speakText("Hello world! Speech synthesis is working seamlessly.", "Kore");
```

---

### React Component Example

```jsx
import React, { useState, useEffect } from "react";

export function GeminiVoicePlayer() {
  const [voices, setVoices] = useState([]);
  const [selectedVoice, setSelectedVoice] = useState("Puck");
  const [text, setText] = useState("Hello! Test my new voice.");
  const [loading, setLoading] = useState(false);
  const [audioUrl, setAudioUrl] = useState(null);

  // Fetch available voices on load
  useEffect(() => {
    fetch("http://localhost:8000/api/v1/audio/gemini-voices")
      .then((res) => res.json())
      .then((data) => setVoices(data.voices || []))
      .catch((err) => console.error("Failed to load voices:", err));
  }, []);

  const handleGenerateSpeech = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://localhost:8000/api/v1/audio/gemini-tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: selectedVoice }),
      });
      const blob = await res.blob();
      setAudioUrl(URL.createObjectURL(blob));
    } catch (err) {
      alert("Error generating speech");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: "20px", fontFamily: "sans-serif" }}>
      <h2>Gemini Text-to-Speech</h2>
      
      <label>Select Voice:</label>
      <select value={selectedVoice} onChange={(e) => setSelectedVoice(e.target.value)}>
        {voices.map((v) => (
          <option key={v.name} value={v.name}>
            {v.name} ({v.gender} - {v.description})
          </option>
        ))}
      </select>

      <br /><br />

      <textarea
        rows={4}
        cols={50}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Enter text to speak..."
      />

      <br /><br />

      <button onClick={handleGenerateSpeech} disabled={loading}>
        {loading ? "Generating Audio..." : "🔊 Speak Text"}
      </button>

      {audioUrl && (
        <div style={{ marginTop: "20px" }}>
          <audio controls src={audioUrl} autoPlay />
        </div>
      )}
    </div>
  );
}
```

---

### cURL Example

```bash
# 1. Fetch Voices List
curl -X GET "http://localhost:8000/api/v1/audio/gemini-voices"

# 2. Download Sample Audio for 'Kore'
curl -o sample_kore.wav "http://localhost:8000/api/v1/audio/gemini-sample?voice=Kore"

# 3. Generate Custom Speech
curl -X POST "http://localhost:8000/api/v1/audio/gemini-tts" \
     -H "Content-Type: application/json" \
     -d '{"text": "Hello from Gemini TTS API", "voice": "Aoede"}' \
     --output speech.wav
```

---

### Python Client Example

```python
import requests

url = "http://localhost:8000/api/v1/audio/gemini-tts"
payload = {
    "text": "Salam! High quality speech generated from python client.",
    "voice": "Charon"
}

response = requests.post(url, json=payload)

if response.status_code == 200:
    with open("output_charon.wav", "wb") as f:
        f.write(response.content)
    print("Saved audio file to output_charon.wav")
else:
    print(f"Error {response.status_code}: {response.text}")
```

---

## 5. Rate Limits & Guidelines

| Limit Type | Free Tier | Paid Tier |
| :--- | :--- | :--- |
| **RPM (Requests/Min)** | 15 Requests / Min | 1,000+ Requests / Min |
| **TPM (Tokens/Min)** | 1,000,000 Tokens / Min | 4,000,000+ Tokens / Min |
| **RPD (Requests/Day)** | 1,500 Requests / Day | Unlimited |
| **Max Output Duration** | ~4 to 8 minutes continuous audio per request | ~4 to 8 minutes continuous audio per request |

---

## 6. Error Handling

| HTTP Status Code | Meaning | Common Cause & Resolution |
| :--- | :--- | :--- |
| `200 OK` | Success | Returns binary `audio/wav` payload. |
| `400 Bad Request` | Invalid Input | Empty text string or invalid JSON structure. |
| `429 Too Many Requests` | Rate Limit Exceeded | Free tier 15 RPM limit reached. Implement retry with exponential backoff. |
| `502 Bad Gateway` | Provider Error | Missing `GEMINI_API_KEY` in server environment or Google API unreachable. |
| `500 Internal Server Error` | Server Error | Internal pipeline exception. Check server logs. |
