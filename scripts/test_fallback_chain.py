import os
import sys
import time
import json
import asyncio
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
import litellm

prompt = 'Return strictly a JSON object: {"queries": [{"query": "deep ocean", "visual_concept": "submarine diving"}]}'

async def test_fallback():
    print("Testing automatic failover when primary model fails (e.g. 429 quota or down)...")
    t0 = time.perf_counter()
    # Primary is a non-existent or rate-limited model -> should seamlessly switch to gpt-oss-20b
    resp = await litellm.acompletion(
        model="groq/non-existent-fake-model",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        fallbacks=[
            {
                "model": "groq/openai/gpt-oss-20b",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            {
                "model": "groq/qwen/qwen3.8-27b",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
        ],
    )
    dt = time.perf_counter() - t0
    print(f"Fallback succeeded in {dt:.3f}s!")
    print("Model actually used:", getattr(resp, "model", "unknown"))
    print("Output:")
    print(resp.choices[0].message.content)

if __name__ == "__main__":
    asyncio.run(test_fallback())
