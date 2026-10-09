import os
import sys
import time
import json
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
import litellm

prompt = """You are an expert AI B-roll video director.
Convert the spoken transcript text into 2 or 3 concrete visual search queries for stock footage engines (Pexels, Pixabay), along with a concise visual concept.

Guidelines:
1. 'queries': 2 or 3 filmable, physical search queries (2-4 words each, e.g. 'deep ocean submarine', 'astronaut in space'). No abstract words.
2. 'visual_concept': Exactly 1 short sentence describing the ideal visual scene for this segment.

Return strictly a JSON object matching this schema:
{
  "queries": ["query 1", "query 2"],
  "visual_concept": "short sentence describing the scene"
}
"""

segment = "In the extreme environments of space and the deep ocean,"

print("=" * 60)
print("Testing Groq (groq/openai/gpt-oss-20b) with queries + visual_concept...")
t0 = time.perf_counter()
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
print(f"Elapsed: {dt:.3f}s")
print(json.dumps(json.loads(resp.choices[0].message.content), indent=2))
