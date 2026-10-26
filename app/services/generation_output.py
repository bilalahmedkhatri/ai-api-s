"""Optional title metadata; never repair or rewrite the generated answer."""

import json
import re
import unicodedata

# This is a transport envelope, not a cover-letter template. The Next.js prompt
# remains the complete task and owns language, facts and writing instructions.
OUTPUT_INSTRUCTIONS = """Complete the user's task exactly, preserving its requested output language and format inside answer.
Return one JSON object with answer (the complete requested text as a string), title (string or null), and title_sources (object or null).
The answer must contain only the complete requested content, without this JSON envelope or an added title/heading. Do not shorten the answer to make room for title metadata.
For an initial cover-letter generation, try to supply a concise title in the requested output language: 3 to 7 words, at most 80 Unicode characters. Use only the supplied job role and/or company, plus ordinary connecting words. Do not invent facts or include the applicant's name, email, phone or other personal/contact information. Do not infer the company from an email address or URL.
title_sources must contain role and company, each either an exact verbatim value supplied in the user's prompt or null. These values must identify the job role/employer, never the applicant. If a role or company cannot be identified safely, use null. Prefer a title whose substantive words come directly from those supplied values; if that cannot be done in the requested language, use title:null.
For review/repair tasks, tasks other than cover letters, or any uncertainty about title language, relevance, privacy or length, return title:null and title_sources:null. A missing title must never prevent a complete answer. Do not put the title, title sources, explanations, citations, or JSON fences into answer unless the user's actual task requests that content.
"""

# Count lexical words, not a standalone em dash; hyphenated job words count once.
_WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_CONTACT = re.compile(r"@|https?://|www\.|(?:\+?\d[\s().-]*){7,}", re.IGNORECASE)
_CONNECTORS = frozenset(
    """application for to at with the a an cover letter
    candidature candidature pour chez au à de du la le les un une lettre motivation
    candidatura para en al el los las una carta presentación presentacion
    candidatura para na no em ao da do uma carta apresentação apresentacao
    отклик на в для компанию компании сопроводительное письмо должность
    """.split()
)


def _words(value):
    return _WORD.findall(unicodedata.normalize("NFC", value).casefold())


def valid_title(candidate, sources, prompt):
    if not isinstance(candidate, str) or not isinstance(sources, dict):
        return None
    title = unicodedata.normalize("NFC", candidate).strip()
    if (
        not title
        or len(title) > 80
        or _CONTACT.search(title)
        or any(
            unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"}
            for char in title
        )
    ):
        return None
    words = _words(title)
    if not 3 <= len(words) <= 7:
        return None
    grounded = set()
    normalized_prompt = unicodedata.normalize("NFC", prompt)
    # Exact evidence plus a word whitelist prevents new entities/adjectives
    # slipping into a title. Unverifiable translations fall back to null.
    for name in ("role", "company"):
        value = sources.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            return None
        value = unicodedata.normalize("NFC", value)
        if (
            value not in normalized_prompt
            or _CONTACT.search(value)
            or any(char in value for char in "\r\n\t:;{}[]<>")
        ):
            return None
        # A quoted applicant value must never be reused as a title source.
        escaped = re.escape(value.strip())
        if re.search(
            r"(?:applicant(?:[ _-]?name)?|candidate(?:[ _-]?name)?|full[ _-]?name|name|имя|кандидат|nombre|nom|nome)"
            r'["\s]*[:=]["\s]*' + escaped + r"(?!\w)",
            normalized_prompt,
            re.IGNORECASE,
        ):
            return None
        grounded.update(_words(value))
    if not grounded or not (set(words) & grounded) or set(words) - grounded - _CONNECTORS:
        return None
    # In addition to whitespace/length bounds, reject model JSON/markup wrappers.
    if any(char in title for char in '{}[]<>`"\\'):
        return None
    return title


def _envelope_text(content):
    raw = content.strip()
    # Tolerate provider fences around the envelope, without changing plain text.
    opening = re.match(r"^```(?:json)?[ \t]*\r?\n", raw, re.IGNORECASE)
    if opening:
        raw = raw[opening.end() :].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    return raw


def complete_envelope_answer(content) -> str | None:
    """Recover only a fully decoded JSON answer, never an unfinished string."""
    if not isinstance(content, str):
        return None
    raw = _envelope_text(content)
    try:
        payload = json.loads(raw)
        answer = payload.get("answer") if isinstance(payload, dict) else None
    except (ValueError, RecursionError):
        answer = None
        if raw.startswith("{"):
            for match in re.finditer(r'(?:^\{\s*|,\s*)"answer"\s*:\s*', raw):
                try:
                    answer, _ = json.JSONDecoder().raw_decode(raw[match.end() :])
                    break
                except (ValueError, RecursionError):
                    continue
    return answer if isinstance(answer, str) and answer.strip() else None


def split_generation_output(content: str, prompt: str) -> tuple[str, str | None]:
    """Keep a usable answer even when optional title metadata is invalid."""
    raw = _envelope_text(content)
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        # If only optional trailing metadata was malformed, a fully decoded
        # leading answer string is still usable. Never heuristically unescape it.
        if raw.startswith("{") and re.search(r'"(?:answer|title|title_sources)"\s*:', raw):
            answer = complete_envelope_answer(raw)
            if answer is not None:
                return answer, None
            raise ValueError("Malformed generation envelope") from None
        return content, None
    if isinstance(payload, dict) and "title" in payload and "answer" not in payload:
        raise ValueError("Missing generation answer")
    if not isinstance(payload, dict) or "answer" not in payload:
        return content, None
    answer = payload["answer"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Empty generation answer")
    # Optional metadata must never invalidate a sound answer.
    try:
        title = valid_title(payload.get("title"), payload.get("title_sources"), prompt)
    except (ValueError, TypeError, RecursionError):
        title = None
    return answer, title
