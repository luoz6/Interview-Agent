import re
import unicodedata

from app.domain.knowledge.models import KnowledgeChunk


_ENGLISH_TECHNICAL_TERM = re.compile(r"[a-z0-9]+(?:\+\+|#)?")
_CJK_TERM = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_TECHNICAL_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "for",
    "with",
    "how",
    "what",
    "why",
}


class KnowledgeReranker:
    """Dependency-free deterministic reranking policy for knowledge chunks."""

    @staticmethod
    def normalize_technical_terms(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        english = {
            term
            for term in _ENGLISH_TECHNICAL_TERM.findall(normalized)
            if term not in _TECHNICAL_STOPWORDS
        }
        return english | set(_CJK_TERM.findall(normalized))

    def rerank(
        self,
        chunks: list[KnowledgeChunk],
        *,
        query_text: str,
        requested_tags: list[str],
        minimum_score: float,
        limit: int,
    ) -> list[KnowledgeChunk]:
        terms = self.normalize_technical_terms(query_text)
        boost_tags = {
            tag.strip().casefold()
            for tag in requested_tags
            if tag and tag.strip() and tag.strip().casefold() != "general"
        }
        ranked: list[KnowledgeChunk] = []
        for chunk in chunks:
            raw_aliases = chunk.metadata.get("aliases", [])
            if isinstance(raw_aliases, str):
                aliases = [raw_aliases]
            elif isinstance(raw_aliases, list):
                aliases = [alias for alias in raw_aliases if isinstance(alias, str)]
            else:
                aliases = []
            searchable = self.normalize_technical_terms(
                " ".join([chunk.title, *aliases])
            )
            exact_boost = 0.06 if terms & searchable else 0.0
            metadata_values = {
                chunk.domain.casefold(),
                *(tag.casefold() for tag in chunk.tags),
            }
            tag_boost = 0.04 if boost_tags & metadata_values else 0.0
            final_score = min(
                1.0,
                max(0.0, float(chunk.score or 0.0) + exact_boost + tag_boost),
            )
            if final_score >= minimum_score:
                ranked.append(chunk.model_copy(update={"score": final_score}))
        return sorted(
            ranked,
            key=lambda item: (-float(item.score or 0.0), item.chunk_id),
        )[: max(0, int(limit))]
