"""app/services/tts/text_utils.py — text chunking for TTS."""

import logging
import re
from functools import lru_cache

import nltk

logger = logging.getLogger(__name__)

# Latin/Hindi/Urdu terminators need trailing whitespace; CJK ones don't.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।۔])\s+|(?<=[。！？])\s*")
_UNICODE_END_RE = re.compile(r"(?<=[।۔])\s+|(?<=[。！？])\s*")


@lru_cache(maxsize=1)
def _punkt():
    try:
        return nltk.tokenize.PunktTokenizer("english")
    except LookupError:
        logger.warning("NLTK punkt_tab missing; using Unicode sentence fallback. Install tokenizer data before deployment.")
        return None


def _sentences(paragraph):
    tokenizer = _punkt()
    if tokenizer is None:
        return _SENTENCE_SPLIT_RE.split(paragraph)
    return [part for sentence in tokenizer.tokenize(paragraph) for part in _UNICODE_END_RE.split(sentence)]


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Split one oversized sentence on spaces (last resort: raw slices)."""
    parts: list[str] = []
    current = ""
    for word in sentence.split():
        while len(word) > max_chars:  # single giant "word"
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:max_chars])
            word = word[max_chars:]
        if len(current) + len(word) + 1 <= max_chars:
            current = f"{current} {word}".strip()
        else:
            parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts


def split_text_into_chunks(text: str, max_chars: int = 400) -> list[str]:
    """Split long text into sentence/paragraph chunks for optimal TTS phonemization."""
    if max_chars < 1:
        raise ValueError("Chunk size must be positive")
    cleaned_text = text.strip()
    if not cleaned_text:
        return []

    paragraphs = [p.strip() for p in cleaned_text.split("\n") if p.strip()]
    chunks: list[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue

        current = ""
        for sentence in (s for s in _sentences(paragraph) if s and s.strip()):
            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(_hard_split(sentence, max_chars))
            elif len(current) + len(sentence) + 1 <= max_chars:
                current = f"{current} {sentence}".strip()
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)

    return chunks if chunks else [cleaned_text]
