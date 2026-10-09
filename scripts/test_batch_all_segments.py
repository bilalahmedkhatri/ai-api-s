import os
import sys
import time
import json
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
import litellm

# Full user payload from Whisper
whisper_data = {
  "transcript": "In the extreme environments of space and the deep ocean, making a single mechanical mistake means only one thing— instant, unavoidable death. You do not get a second chance. So how do fragile humans survive out there? The engineering required for both places is completely opposite. In the deep ocean, your greatest enemy is hydrostatic pressure. To survive, submarines must be built using incredibly thick spheres of titanium and steel just to stop the water from crushing them. If there is even a microscopic crack in the glass, the entire submarine will violently implode in a microsecond. In space, your ultimate enemies are the absolute vacuum and extreme temperatures. A spacecraft must be perfectly sealed so the internal pressure does not violently leak out. At the exact same time, a spacesuit must protect you from boiling 250-degree heat, freezing cold, and lethal cosmic rays that can shatter your DNA. In the ocean, we must fight with all our might to keep the outside force from getting in. In space, we must desperately trap our fragile bubble of life from escaping out. Think about it. Our Earth is a rare, miraculous haven where we survive without a capsule. Do we truly appreciate this gift? What do you think?",
  "segments": [
    {"id": 0, "duration": 3.78, "text": "In the extreme environments of space and the deep ocean,"},
    {"id": 1, "duration": 4.72, "text": "making a single mechanical mistake means only one thing—"},
    {"id": 2, "duration": 2.38, "text": "instant, unavoidable death."},
    {"id": 3, "duration": 2.74, "text": "You do not get a second chance."},
    {"id": 4, "duration": 3.3, "text": "So how do fragile humans survive out there?"},
    {"id": 5, "duration": 4.42, "text": "The engineering required for both places is completely opposite."},
    {"id": 6, "duration": 4.46, "text": "In the deep ocean, your greatest enemy is hydrostatic pressure."},
    {"id": 7, "duration": 6.0, "text": "To survive, submarines must be built using incredibly thick spheres of titanium and steel"},
    {"id": 8, "duration": 2.76, "text": "just to stop the water from crushing them."},
    {"id": 9, "duration": 3.2, "text": "If there is even a microscopic crack in the glass,"},
    {"id": 10, "duration": 4.3, "text": "the entire submarine will violently implode in a microsecond."},
    {"id": 11, "duration": 5.28, "text": "In space, your ultimate enemies are the absolute vacuum and extreme temperatures."},
    {"id": 12, "duration": 5.68, "text": "A spacecraft must be perfectly sealed so the internal pressure does not violently leak out."},
    {"id": 13, "duration": 5.8, "text": "At the exact same time, a spacesuit must protect you from boiling 250-degree heat,"},
    {"id": 14, "duration": 4.66, "text": "freezing cold, and lethal cosmic rays that can shatter your DNA."},
    {"id": 15, "duration": 5.38, "text": "In the ocean, we must fight with all our might to keep the outside force from getting in."},
    {"id": 16, "duration": 5.34, "text": "In space, we must desperately trap our fragile bubble of life from escaping out."},
    {"id": 17, "duration": 1.0, "text": "Think about it."},
    {"id": 18, "duration": 5.0, "text": "Our Earth is a rare, miraculous haven where we survive without a capsule."},
    {"id": 19, "duration": 2.58, "text": "Do we truly appreciate this gift?"},
    {"id": 20, "duration": 1.0, "text": "What do you think?"}
  ]
}

SYSTEM_PROMPT = """You are an expert AI B-roll video director.
You are given the full narration context and a list of timestamped segments from an audio/video file.

Your task:
For EACH segment, provide 2 concrete, filmable visual stock search queries (2-4 words each) and a matching visual concept for that specific query.

Guidelines:
1. Use the full transcript context to ensure visual continuity across the entire video.
2. Even for short phrases (like 'Think about it' or 'What do you think?'), choose visuals that match the surrounding theme (e.g. Earth from orbit, beautiful planet, deep sea).
3. 'query': 2 to 4 words. Physical nouns and settings for Pexels/Pixabay (e.g. 'deep ocean submarine', 'astronaut spacewalk earth'). No abstract words.
4. 'visual_concept': 1 short sentence describing the exact scene for that specific query.

Return strictly a JSON object matching this schema:
{
  "segments": [
    {
      "id": 0,
      "queries": [
        {
          "query": "space station exterior",
          "visual_concept": "Wide shot of the International Space Station orbiting in outer space."
        },
        {
          "query": "deep ocean trench",
          "visual_concept": "Dark underwater footage of a deep sea trench with bioluminescent creatures."
        }
      ]
    }
  ]
}
"""

user_content = f"""Full Video Transcript:
"{whisper_data['transcript']}"

Segments to process:
{json.dumps([{'id': s['id'], 'text': s['text'], 'duration': s['duration']} for s in whisper_data['segments']], indent=2)}
"""

print("Running batch keyword generation across ALL 21 segments with groq/openai/gpt-oss-20b...")
t0 = time.perf_counter()
resp = litellm.completion(
    model="groq/openai/gpt-oss-20b",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ],
    response_format={"type": "json_object"},
    temperature=0.2,
)
dt = time.perf_counter() - t0
print(f"Elapsed Time for all 21 segments: {dt:.3f}s")
data = json.loads(resp.choices[0].message.content)
print(f"Total processed segments returned: {len(data.get('segments', []))}")
print("Sample segment 0:")
print(json.dumps(data['segments'][0], indent=2))
print("Sample segment 17 ('Think about it'):")
print(json.dumps(data['segments'][17], indent=2))
