import os
import sys
import time
import json
from dotenv import load_dotenv

# Ensure utf-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()
import litellm

prompt = """You are an expert AI B-roll video director.
Convert spoken transcript text into concrete, high-converting visual search keywords for stock footage engines (Pexels, Pixabay).

Guidelines:
1. Identify the strongest visual imagery in the text (settings, objects, actions).
2. Avoid abstract words, grammatical filler, emotions, or metaphors (e.g. avoid 'extreme environments', 'challenges', 'innovation').
3. Produce physical, filmable search terms (e.g. 'deep ocean submarine underwater', 'outer space astronaut earth').
4. Return strictly a JSON object with:
   - "primary_query": Best 2-4 word visual search query.
   - "alternate_queries": Array of 2 alternative 2-4 word queries.
   - "visual_concept": 1 short sentence describing the ideal video shot.
"""

segment = "In the extreme environments of space and the deep ocean,"

# Test Groq openai/gpt-oss-20b
print("=" * 60)
print("1. Testing Groq (openai/gpt-oss-20b)...")
t0 = time.perf_counter()
try:
    resp = litellm.completion(
        model="groq/openai/gpt-oss-20b",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f'Spoken segment: "{segment}"'},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    dt = time.perf_counter() - t0
    content = resp.choices[0].message.content
    print(f"Groq elapsed: {dt:.3f}s")
    print(json.dumps(json.loads(content), indent=2))
except Exception as e:
    print("Groq err:", e)

# Test Groq qwen/qwen3.8-27b
print("=" * 60)
print("2. Testing Groq (qwen/qwen3.8-27b)...")
t0 = time.perf_counter()
try:
    resp = litellm.completion(
        model="groq/qwen/qwen3.8-27b",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f'Spoken segment: "{segment}"'},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    dt = time.perf_counter() - t0
    content = resp.choices[0].message.content
    print(f"Groq Qwen elapsed: {dt:.3f}s")
    print(json.dumps(json.loads(content), indent=2))
except Exception as e:
    print("Groq Qwen err:", e)

# Test Gemini 2.5 flash lite
print("=" * 60)
print("3. Testing Gemini (gemini/gemini-2.5-flash-lite)...")
t0 = time.perf_counter()
try:
    resp = litellm.completion(
        model="gemini/gemini-2.5-flash-lite",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f'Spoken segment: "{segment}"'},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    dt = time.perf_counter() - t0
    content = resp.choices[0].message.content
    print(f"Gemini elapsed: {dt:.3f}s")
    print(json.dumps(json.loads(content), indent=2))
except Exception as e:
    print("Gemini err:", e)
