import os
import sys
import time
import json
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
import litellm

transcript = (
    "In the extreme environments of space and the deep ocean, making a single mechanical mistake "
    "means only one thing— instant, unavoidable death. You do not get a second chance. So how do fragile "
    "humans survive out there? The engineering required for both places is completely opposite. In the "
    "deep ocean, your greatest enemy is hydrostatic pressure. To survive, submarines must be built using "
    "incredibly thick spheres of titanium and steel just to stop the water from crushing them. If there is "
    "even a microscopic crack in the glass, the entire submarine will violently implode in a microsecond. "
    "In space, your ultimate enemies are the absolute vacuum and extreme temperatures. A spacecraft must "
    "be perfectly sealed so the internal pressure does not violently leak out. At the exact same time, "
    "a spacesuit must protect you from boiling 250-degree heat, freezing cold, and lethal cosmic rays that "
    "can shatter your DNA. In the ocean, we must fight with all our might to keep the outside force from "
    "getting in. In space, we must desperately trap our fragile bubble of life from escaping out. "
    "Think about it. Our Earth is a rare, miraculous haven where we survive without a capsule. "
    "Do we truly appreciate this gift? What do you think?"
)

SYSTEM_PROMPT = f"""You are an expert AI B-roll video director for a documentary.
Overall Video Story: "{transcript}"

Task:
Convert the current spoken speech segment into 2 concrete visual stock search queries (Pexels, Pixabay).
For EACH query, provide its specific visual concept.

Rules:
1. Always maintain visual continuity with the overall space & deep ocean documentary.
2. Even for short phrases (like 'Think about it' or 'What do you think?'), generate cinematic visuals matching the overarching theme.
3. 'query': 2 to 4 words. Physical, filmable nouns and settings.
4. 'visual_concept': 1 short sentence describing the exact scene.

Return strictly a JSON object matching this schema:
{{
  "queries": [
    {{"query": "query 1", "visual_concept": "concept 1"}},
    {{"query": "query 2", "visual_concept": "concept 2"}}
  ]
}}"""

test_segments = [
    {"id": 0, "text": "In the extreme environments of space and the deep ocean,"},
    {"id": 17, "text": "Think about it."},
    {"id": 20, "text": "What do you think?"},
]

for s in test_segments:
    t0 = time.perf_counter()
    resp = litellm.completion(
        model="groq/openai/gpt-oss-20b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Segment {s['id']}: '{s['text']}'"},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    dt = time.perf_counter() - t0
    print(f"=== Segment {s['id']}: '{s['text']}' (took {dt:.2f}s) ===")
    print(json.dumps(json.loads(resp.choices[0].message.content), indent=2))
    print()
