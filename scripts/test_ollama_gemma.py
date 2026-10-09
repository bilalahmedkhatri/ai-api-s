import time
import httpx

prompt = """You are an expert AI B-roll video director. Convert spoken transcript text into concrete, high-converting visual search keywords for stock footage engines (Pexels, Pixabay).
Return strictly a JSON object with:
- "primary_query": Best 2-4 word visual search query.
- "alternate_queries": Array of 2 alternative 2-4 word queries.
- "visual_concept": 1 short sentence describing the ideal video shot.
"""

segment = "In the extreme environments of space and the deep ocean,"

print("Testing Ollama gemma4:latest...")
t0 = time.perf_counter()
try:
    r = httpx.post(
        "http://localhost:11434/api/chat",
        json={
            "model": "gemma4:latest",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f'Spoken segment: "{segment}"'},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 100
            }
        },
        timeout=30.0
    )
    print(f"Status: {r.status_code}, time: {time.perf_counter() - t0:.2f}s")
    if r.status_code == 200:
        print(r.json().get("message", {}).get("content", ""))
    else:
        print(r.text)
except Exception as e:
    print(f"Error: {e}")
