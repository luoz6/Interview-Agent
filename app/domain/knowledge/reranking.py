import re
import unicodedata

from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    CandidateRankingExplanation,
    RetrievalCandidate,
)


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

    def rerank_candidates(
        self,
        candidates: list[RetrievalCandidate],
        *,
        query_text: str,
        requested_tags: list[str],
        minimum_score: float,
        limit: int,
    ) -> list[RetrievalCandidate]:
        """Rerank fused candidates without discarding their ranking policy.

        The raw provider score remains the minimum-score eligibility signal.
        Ordering is based on the fusion score (or the best available channel
        score when fusion is absent), plus the same deterministic exact-term
        and routing-tag boosts used by the compatibility reranker. This makes
        Fusion -> Rerank explicit and prevents a raw embedding score from
        silently replacing RRF/rank-normalized order.
        """

        terms = self.normalize_technical_terms(query_text)
        boost_tags = {
            tag.strip().casefold()
            for tag in requested_tags
            if tag and tag.strip() and tag.strip().casefold() != "general"
        }
        ranked: list[RetrievalCandidate] = []
        for candidate in candidates:
            chunk = candidate.chunk
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
            raw_score = float(chunk.score or 0.0)
            eligibility_score = min(1.0, max(0.0, raw_score + exact_boost + tag_boost))
            if eligibility_score < minimum_score:
                continue
            score_sources = (
                ("fusion_score", candidate.fusion_score),
                ("semantic_score", candidate.semantic_score),
                ("lexical_score", candidate.lexical_score),
                ("chunk_score", chunk.score),
            )
            base_score_source, source_score = next(
                ((name, score) for name, score in score_sources if score is not None),
                ("chunk_score", 0.0),
            )
            policy_score = float(source_score)
            rerank_score = policy_score + exact_boost + tag_boost
            ranked.append(
                candidate.model_copy(
                    update={
                        "chunk": chunk.model_copy(update={"score": rerank_score}),
                        "rerank_score": rerank_score,
                        "ranking_explanation": CandidateRankingExplanation(
                            base_score_source=base_score_source,
                            base_score=policy_score,
                            exact_term_boost=exact_boost,
                            routing_tag_boost=tag_boost,
                            eligibility_score=eligibility_score,
                            eligible=True,
                            final_rerank_score=rerank_score,
                            tie_break_fusion_rank=candidate.fusion_rank,
                            reason_codes=("eligible",),
                        ),
                    }
                )
            )
        ordered = sorted(
            ranked,
            key=lambda item: (
                -float(item.rerank_score or 0.0),
                item.fusion_rank or 10**9,
                item.chunk_id,
            ),
        )[: max(0, int(limit))]
        return [
            candidate.model_copy(update={"rerank_rank": rank})
            for rank, candidate in enumerate(ordered, 1)
        ]
