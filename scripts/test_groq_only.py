import os
import time
import json
from dotenv import load_dotenv

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

print("Calling Groq llama-3.1-8b-instant...")
t0 = time.perf_counter()
resp = litellm.completion(
    model="groq/llama-3.1-8b-instant",
    messages=[
        {"role": "system", "content": prompt},
        {"role": "user", "content": f'Spoken segment: "{segment}"'},
    ],
    response_format={"type": "json_object"},
    temperature=0.2,
)
dt = time.perf_counter() - t0
print(f"Elapsed: {dt:.3f}s")
print(resp.choices[0].message.content)
