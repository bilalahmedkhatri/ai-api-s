"""app/services/keyword_extractor.py — Extract visual B-roll stock search queries and concepts using LLMs with DB key rotation."""

import json
import logging
import time

import litellm
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.key_manager import (
    get_key_for_model,
    get_next_available_key,
    mark_key_quota_exceeded,
    mark_key_used,
)

logger = logging.getLogger(__name__)

DEFAULT_KEYWORD_MODEL = "groq/openai/gpt-oss-20b"


class VisualQueryItem(BaseModel):
    query: str = Field(..., description="2-4 word concrete stock video search query")
    visual_concept: str = Field(..., description="Short visual scene description for this query")


class KeywordExtractionResult(BaseModel):
    queries: list[VisualQueryItem] = Field(default_factory=list)


def build_system_prompt(overall_context: str | None = None) -> str:
    context_str = f'\nOverall Video Story Context:\n"{overall_context}"\n' if overall_context else ""
    return f"""You are an expert AI B-roll video director.
{context_str}
Task:
Convert the current spoken speech segment into 2 or 3 concrete visual stock search queries (Pexels, Pixabay).
For EACH query, provide its specific visual concept.

Rules:
1. Always maintain visual continuity with the overall video story.
2. Even for short phrases (like 'Think about it' or 'What do you think?'), generate cinematic visuals matching the overarching theme.
3. 'query': 2 to 4 words. Physical, filmable nouns and settings (e.g. 'space station exterior', 'deep ocean submarine'). NO abstract words (avoid 'extreme environments', 'challenge', 'future').
4. 'visual_concept': Exactly 1 short sentence describing the exact visual scene for this specific query.

Return strictly a JSON object matching this schema:
{{
  "queries": [
    {{
      "query": "2-4 word search keyword",
      "visual_concept": "1 sentence description of this specific scene"
    }}
  ]
}}"""


async def extract_visual_queries(
    segment_text: str,
    db: AsyncSession,
    context: str | None = None,
    model_name: str = DEFAULT_KEYWORD_MODEL,
    max_retries: int = 2,
) -> KeywordExtractionResult:
    """
    Extract paired visual queries and concepts from spoken segment text.
    Uses DB-backed provider key rotation (via ProviderAPIKey table) and tracks key quota via mark_key_used.
    """
    system_prompt = build_system_prompt(context)
    user_prompt = f'Spoken segment: "{segment_text}"'

    # Determine provider from model_name
    provider = model_name.split("/", maxsplit=1)[0] if "/" in model_name else "groq"

    # Resolve API key from DB with fallback to environment
    active_key_id: int | None = None
    active_key: str | None = None

    key_info = await get_key_for_model(db, model_name)
    if key_info:
        active_key_id, active_key = key_info
    else:
        # Fallback to .env setting
        active_key = settings.groq_api_key or None

    for attempt in range(max_retries + 1):
        try:
            t0 = time.perf_counter()
            resp = await litellm.acompletion(
                model=model_name,
                api_key=active_key,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            elapsed = time.perf_counter() - t0

            # Mark key used in DB for tracking quota and requests_today
            if active_key_id is not None:
                await mark_key_used(db, active_key_id)
                logger.info(
                    "Extracted keywords in %.2fs using model=%s (key_id=%d updated)",
                    elapsed,
                    model_name,
                    active_key_id,
                )

            content = resp.choices[0].message.content
            parsed = json.loads(content)
            raw_queries = parsed.get("queries", [])
            items = [
                VisualQueryItem(
                    query=q.get("query", "").strip(),
                    visual_concept=q.get("visual_concept", "").strip(),
                )
                for q in raw_queries
                if q.get("query")
            ]
            return KeywordExtractionResult(queries=items)

        except Exception as exc:
            err_str = str(exc).lower()
            if ("429" in err_str or "quota" in err_str or "rate limit" in err_str) and active_key_id is not None:
                logger.warning("Key id=%d quota exceeded on %s: %s", active_key_id, model_name, exc)
                await mark_key_quota_exceeded(db, active_key_id)
                next_key = await get_next_available_key(db, provider)
                if next_key and attempt < max_retries:
                    active_key_id, active_key = next_key
                    logger.info("Rotated to next available key id=%d for attempt %d", active_key_id, attempt + 1)
                    continue

            logger.error("Keyword extraction failed for model=%s: %s", model_name, exc)
            raise exc

    return KeywordExtractionResult(queries=[])
