from __future__ import annotations

import re
import unicodedata

from app.domain.knowledge.models import KnowledgeChunk


_LATIN_TECHNICAL_TERM = re.compile(
    r"[a-z0-9]+(?:(?:\+{1,2}|#)(?:[a-z0-9]+)?|[._-][a-z0-9]+)*",
    re.IGNORECASE,
)
_CJK_SEQUENCE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "how",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "why",
    "与",
    "如何",
    "什么",
}


def normalize_lexical_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def extract_technical_terms(text: str) -> set[str]:
    normalized = normalize_lexical_text(text)
    latin = {
        term
        for term in _LATIN_TECHNICAL_TERM.findall(normalized)
        if term not in _STOPWORDS
    }
    cjk = {
        term for term in _CJK_SEQUENCE.findall(normalized) if term not in _STOPWORDS
    }
    return latin | cjk


def chunk_lexical_terms(chunk: KnowledgeChunk) -> set[str]:
    aliases = chunk.metadata.get("aliases", [])
    technical_terms = chunk.metadata.get("technical_terms", [])
    normalized_title_terms = chunk.metadata.get("normalized_title_terms", [])
    # Domain and tags are routing signals. Treating them as exact lexical
    # terms makes every chunk in a routed domain a false-positive match.
    values: list[str] = [chunk.title]
    for raw in (aliases, technical_terms, normalized_title_terms):
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(item for item in raw if isinstance(item, str))
    return extract_technical_terms(" ".join(values))


def lexical_match_score(
    query_terms: set[str],
    chunk: KnowledgeChunk,
) -> tuple[float, list[str]]:
    if not query_terms:
        return 0.0, []
    matched = sorted(query_terms & chunk_lexical_terms(chunk))
    if not matched:
        return 0.0, []
    coverage = len(matched) / len(query_terms)
    exact_aliases = {
        normalize_lexical_text(item)
        for item in chunk.metadata.get("aliases", [])
        if isinstance(item, str)
    }
    normalized_query_terms = {normalize_lexical_text(item) for item in query_terms}
    alias_bonus = 0.2 if exact_aliases & normalized_query_terms else 0.0
    return min(1.0, 0.5 + 0.5 * coverage + alias_bonus), matched
