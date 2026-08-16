"""Exact-match rule engine with SQLite-based 1-hour query cache.

Fast path: regex pattern matching → immediate routing decision.
Pre-cache:  MD5 hash lookup against FinalResponse table (1-hour TTL).

Ponytail note: NLTK/spaCy were spec'd but rejected (Step 3 — stdlib `re`
handles all pattern matching; Step 5 — no new deps needed).
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import FinalResponse

logger = logging.getLogger(__name__)


# ── Intent patterns ────────────────────────────────────────────────────────────
# Each tuple: (compiled_regex, intent_label, requires_llm, requires_search)
_RULES: list[tuple[re.Pattern, str, bool, bool]] = [
    # Real-time lookups — need search, skip LLM
    (re.compile(r"\b(weather|temperature|forecast)\b.{0,30}\bin\b", re.I), "weather", False, True),
    (re.compile(r"\b(price|cost|how much).{0,20}\bof\b", re.I), "pricing", False, True),
    (re.compile(r"\bcurrent\s+time\b|\bwhat\s+time\s+is\s+it\b", re.I), "time_lookup", False, False),
    (re.compile(r"\blatest\s+news\b|\bbreaking\s+news\b", re.I), "news", False, True),
    (re.compile(r"\bwho\s+is\s+(the\s+)?(ceo|president|prime\s+minister|founder)\b", re.I), "factual_person", False, True),
    # Conversational — no search needed
    (re.compile(r"^\s*(hi|hello|hey|good\s+(morning|evening|night))[!.,]?\s*$", re.I), "greeting", False, False),
    (re.compile(r"\b(thank\s*you|thanks)\b", re.I), "thanks", False, False),
    # Math — LLM only, no search
    (re.compile(r"\b\d+\s*[\+\-\*\/\^]\s*\d+\b", re.I), "math_expression", True, False),
    (re.compile(r"\b(calculate|compute|solve|integral|derivative)\b", re.I), "math_task", True, False),
]


@dataclass
class RuleResult:
    matched: bool
    pattern: str | None
    intent: str
    requires_llm: bool
    requires_search: bool
    # Populated only on cache hit
    cached_answer: str | None = None
    cache_hit: bool = False


def _md5(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


async def _check_cache(query: str, db: AsyncSession) -> str | None:
    """Return a cached FinalResponse answer if it exists within the last hour."""
    q_hash = _md5(query)
    cutoff = datetime.now(UTC) - timedelta(hours=1)

    stmt = (
        select(FinalResponse)
        .where(FinalResponse.created_at >= cutoff)
        .order_by(FinalResponse.created_at.desc())
        .limit(10)
    )
    rows = (await db.execute(stmt)).scalars().all()

    for row in rows:
        meta = row.meta  # uses the @property on the model
        if meta.get("query_hash") == q_hash:
            logger.info("Cache hit for query hash %s", q_hash)
            return row.generated_output

    return None


async def run_rule_engine(query: str, db: AsyncSession) -> RuleResult:
    """
    1. Check SQLite cache (1-hour TTL via MD5 hash).
    2. Match regex rules.
    3. Return RuleResult — matched=False means pass to semantic router.
    """
    # ── Step 1: cache lookup ───────────────────────────────────────────────────
    cached = await _check_cache(query, db)
    if cached:
        return RuleResult(
            matched=True,
            pattern="cache_hit",
            intent="cached",
            requires_llm=False,
            requires_search=False,
            cached_answer=cached,
            cache_hit=True,
        )

    # ── Step 2: regex rules ────────────────────────────────────────────────────
    for pattern, intent, requires_llm, requires_search in _RULES:
        if pattern.search(query):
            logger.info("Rule matched: intent=%s pattern=%s", intent, pattern.pattern[:40])
            return RuleResult(
                matched=True,
                pattern=pattern.pattern,
                intent=intent,
                requires_llm=requires_llm,
                requires_search=requires_search,
            )

    return RuleResult(matched=False, pattern=None, intent="unknown",
                      requires_llm=True, requires_search=True)
