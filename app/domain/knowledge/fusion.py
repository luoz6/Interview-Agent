from __future__ import annotations

from collections import defaultdict

from app.domain.knowledge.retrieval import RetrievalCandidate


def fuse_retrieval_candidates(
    semantic: list[RetrievalCandidate],
    lexical: list[RetrievalCandidate],
    *,
    strategy: str,
    k: int,
    semantic_weight: float,
    lexical_weight: float,
    limit: int,
) -> list[RetrievalCandidate]:
    if strategy == "weighted_rrf":
        return weighted_reciprocal_rank_fusion(
            semantic,
            lexical,
            k=k,
            semantic_weight=semantic_weight,
            lexical_weight=lexical_weight,
            limit=limit,
        )
    if strategy == "rank_normalized_score":
        return rank_normalized_score_fusion(
            semantic,
            lexical,
            semantic_weight=semantic_weight,
            lexical_weight=lexical_weight,
            limit=limit,
        )
    raise ValueError(f"unsupported retrieval fusion strategy: {strategy}")


def weighted_reciprocal_rank_fusion(
    semantic: list[RetrievalCandidate],
    lexical: list[RetrievalCandidate],
    *,
    k: int,
    semantic_weight: float,
    lexical_weight: float,
    limit: int,
) -> list[RetrievalCandidate]:
    if k < 1:
        raise ValueError("RRF k must be positive")
    if semantic_weight <= 0 or lexical_weight <= 0:
        raise ValueError("RRF channel weights must be positive")

    by_id: dict[str, RetrievalCandidate] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    hits: defaultdict[str, set[str]] = defaultdict(set)
    matched_terms: defaultdict[str, set[str]] = defaultdict(set)

    for channel, candidates, weight in (
        ("semantic", semantic, semantic_weight),
        ("lexical", lexical, lexical_weight),
    ):
        for fallback_rank, candidate in enumerate(candidates, 1):
            rank = (
                candidate.semantic_rank
                if channel == "semantic"
                else candidate.lexical_rank
            ) or fallback_rank
            chunk_id = candidate.chunk_id
            scores[chunk_id] += weight / (k + rank)
            hits[chunk_id].add(channel)
            matched_terms[chunk_id].update(candidate.matched_terms)
            existing = by_id.get(chunk_id)
            if existing is None:
                by_id[chunk_id] = candidate
            else:
                by_id[chunk_id] = existing.model_copy(
                    update={
                        "semantic_score": existing.semantic_score
                        if existing.semantic_score is not None
                        else candidate.semantic_score,
                        "semantic_rank": existing.semantic_rank
                        if existing.semantic_rank is not None
                        else candidate.semantic_rank,
                        "lexical_score": existing.lexical_score
                        if existing.lexical_score is not None
                        else candidate.lexical_score,
                        "lexical_rank": existing.lexical_rank
                        if existing.lexical_rank is not None
                        else candidate.lexical_rank,
                    }
                )

    ordered_ids = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
    return [
        by_id[chunk_id].model_copy(
            update={
                "fusion_score": scores[chunk_id],
                "fusion_rank": rank,
                "channel_hits": sorted(hits[chunk_id]),
                "matched_terms": sorted(matched_terms[chunk_id]),
            }
        )
        for rank, chunk_id in enumerate(ordered_ids, 1)
    ]


def rank_normalized_score_fusion(
    semantic: list[RetrievalCandidate],
    lexical: list[RetrievalCandidate],
    *,
    semantic_weight: float,
    lexical_weight: float,
    limit: int,
) -> list[RetrievalCandidate]:
    """Fuse channel ranks on a deterministic [0, 1] scale.

    Rank normalization avoids comparing provider-specific raw scores. A single
    result receives 1.0; otherwise rank 1 receives 1.0 and the final result 0.0.
    """
    if semantic_weight <= 0 or lexical_weight <= 0:
        raise ValueError("fusion channel weights must be positive")
    if limit < 1:
        return []

    by_id: dict[str, RetrievalCandidate] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    hits: defaultdict[str, set[str]] = defaultdict(set)
    matched_terms: defaultdict[str, set[str]] = defaultdict(set)
    for channel, candidates, weight in (
        ("semantic", semantic, semantic_weight),
        ("lexical", lexical, lexical_weight),
    ):
        count = len(candidates)
        for fallback_rank, candidate in enumerate(candidates, 1):
            rank = (
                candidate.semantic_rank
                if channel == "semantic"
                else candidate.lexical_rank
            ) or fallback_rank
            normalized = 1.0 if count == 1 else max(0.0, (count - rank) / (count - 1))
            chunk_id = candidate.chunk_id
            scores[chunk_id] += weight * normalized
            hits[chunk_id].add(channel)
            matched_terms[chunk_id].update(candidate.matched_terms)
            existing = by_id.get(chunk_id)
            if existing is None:
                by_id[chunk_id] = candidate
            else:
                by_id[chunk_id] = existing.model_copy(
                    update={
                        "semantic_score": existing.semantic_score
                        if existing.semantic_score is not None
                        else candidate.semantic_score,
                        "semantic_rank": existing.semantic_rank
                        if existing.semantic_rank is not None
                        else candidate.semantic_rank,
                        "lexical_score": existing.lexical_score
                        if existing.lexical_score is not None
                        else candidate.lexical_score,
                        "lexical_rank": existing.lexical_rank
                        if existing.lexical_rank is not None
                        else candidate.lexical_rank,
                    }
                )

    ordered_ids = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
    return [
        by_id[chunk_id].model_copy(
            update={
                "fusion_score": scores[chunk_id],
                "fusion_rank": rank,
                "channel_hits": sorted(hits[chunk_id]),
                "matched_terms": sorted(matched_terms[chunk_id]),
            }
        )
        for rank, chunk_id in enumerate(ordered_ids, 1)
    ]
