from __future__ import annotations

from time import perf_counter

from app.domain.knowledge.lexical import (
    extract_technical_terms,
    lexical_match_score,
)
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelResult,
    RetrievalChannelTrace,
    RetrievalRequest,
)
from app.ports.knowledge import LexicalCandidateSource


class ExactTermLexicalRetriever:
    """Dependency-free exact technical-term and alias retrieval policy."""

    def __init__(self, source: LexicalCandidateSource) -> None:
        self._source = source

    def retrieve_lexical(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        started_at = perf_counter()
        try:
            chunks = self._source.load_active_candidates(
                source_types=list(request.hard_constraints.source_types) or None
            )
        except Exception:
            latency = round((perf_counter() - started_at) * 1000, 3)
            return RetrievalChannelResult(
                availability=RetrievalAvailability.UNAVAILABLE,
                trace=RetrievalChannelTrace(
                    channel="lexical",
                    status="unavailable",
                    latency_ms=latency,
                    candidate_count=0,
                    reason_code="lexical_unavailable",
                ),
            )

        query_terms = extract_technical_terms(request.query_text)
        raw_hard_domains = request.hard_constraints.filters.get("domains", ())
        if isinstance(raw_hard_domains, str):
            raw_hard_domains = [raw_hard_domains]
        hard_domains = {
            str(item).strip().casefold()
            for item in raw_hard_domains
            if str(item).strip()
        }
        raw_hard_tags = request.hard_constraints.filters.get("tags", ())
        if isinstance(raw_hard_tags, str):
            raw_hard_tags = [raw_hard_tags]
        hard_tags = {
            str(item).strip().casefold()
            for item in raw_hard_tags
            if str(item).strip()
        }
        if request.hard_constraints.filters.get("include_general_tag"):
            hard_tags.add("general")
        scored = []
        for chunk in chunks:
            if hard_domains and chunk.domain.casefold() not in hard_domains:
                continue
            if hard_tags and not hard_tags.intersection(
                item.casefold() for item in chunk.tags
            ):
                continue
            score, matched = lexical_match_score(query_terms, chunk)
            if score > 0:
                scored.append((score, chunk, matched))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        candidates = [
            RetrievalCandidate(
                chunk=chunk.model_copy(update={"score": score}),
                lexical_score=score,
                lexical_rank=rank,
                channel_hits=["lexical"],
                matched_terms=matched,
            )
            for rank, (score, chunk, matched) in enumerate(
                scored[: max(1, candidate_limit)], 1
            )
        ]
        latency = round((perf_counter() - started_at) * 1000, 3)
        return RetrievalChannelResult(
            availability=RetrievalAvailability.AVAILABLE,
            candidates=candidates,
            trace=RetrievalChannelTrace(
                channel="lexical",
                status="completed" if candidates else "empty",
                latency_ms=latency,
                candidate_count=len(candidates),
                hit_ids=[item.chunk_id for item in candidates],
            ),
        )
