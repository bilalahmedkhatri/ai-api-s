import os
import sys
import time
import json
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
import litellm

# Test 1: Single segment with paired query + visual_concept
PROMPT_SINGLE = """You are an expert AI B-roll video director.
Context of the full video: A documentary contrasting the extreme lethal environments of outer space vs deep ocean engineering.

Task:
Convert the given spoken segment into 2 or 3 concrete visual search queries for stock footage engines (Pexels, Pixabay).
For each query, provide its specific visual concept.

Rules:
1. 'query': 2 to 4 words. Physical, filmable nouns and environments (e.g. 'deep ocean submarine', 'astronaut in space'). No abstract words.
2. 'visual_concept': 1 short sentence describing the exact scene for this specific query.

Return strictly a JSON object matching this schema:
{
  "queries": [
    {
      "query": "short 2-4 word stock search keyword",
      "visual_concept": "1 sentence description of this specific visual scene"
    }
  ]
}
"""

segment_0 = "In the extreme environments of space and the deep ocean,"

print("=" * 60)
print("1. Testing Single Segment with Paired Queries + Visual Concept...")
t0 = time.perf_counter()
resp = litellm.completion(
    model="groq/openai/gpt-oss-20b",
    messages=[
        {"role": "system", "content": PROMPT_SINGLE},
        {"role": "user", "content": f'Spoken segment: "{segment_0}"'},
    ],
    response_format={"type": "json_object"},
    temperature=0.2,
)
print(f"Time: {time.perf_counter() - t0:.3f}s")
print(json.dumps(json.loads(resp.choices[0].message.content), indent=2))
