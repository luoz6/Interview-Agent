from __future__ import annotations

import math
from time import perf_counter

from app.domain.knowledge.lexical import (
    extract_technical_terms,
    lexical_match_score,
)
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelResult,
    RetrievalChannelTrace,
    RetrievalRequest,
)


class SourceAwareKnowledgeRetriever:
    """Merge System and frozen User candidates inside the two existing channels."""

    def __init__(
        self,
        system_semantic,
        system_lexical,
        *,
        user_chunks_factory,
        embedding_provider,
    ) -> None:
        self._system_semantic = system_semantic
        self._system_lexical = system_lexical
        self._user_chunks_factory = user_chunks_factory
        self._embedding_provider = embedding_provider

    def retrieve_semantic(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        scope = request.source_scope
        if scope is None or (
            scope.include_system_knowledge and not scope.selected_documents
        ):
            return self._system_semantic.retrieve_semantic(
                request,
                candidate_limit=candidate_limit,
            )
        started_at = perf_counter()
        results = []
        if scope.include_system_knowledge:
            results.append(
                self._system_semantic.retrieve_semantic(
                    request,
                    candidate_limit=candidate_limit,
                )
            )
        if scope.selected_documents:
            results.append(
                self._retrieve_user_semantic(
                    request,
                    candidate_limit=candidate_limit,
                )
            )
        if not results:
            return _empty_channel("semantic", started_at=started_at)
        return _merge_channel_results(
            "semantic",
            results,
            candidate_limit=candidate_limit,
            started_at=started_at,
        )

    def retrieve_lexical(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        scope = request.source_scope
        if scope is None or (
            scope.include_system_knowledge and not scope.selected_documents
        ):
            return self._system_lexical.retrieve_lexical(
                request,
                candidate_limit=candidate_limit,
            )
        started_at = perf_counter()
        results = []
        if scope.include_system_knowledge:
            results.append(
                self._system_lexical.retrieve_lexical(
                    request,
                    candidate_limit=candidate_limit,
                )
            )
        if scope.selected_documents:
            results.append(
                self._retrieve_user_lexical(
                    request,
                    candidate_limit=candidate_limit,
                )
            )
        if not results:
            return _empty_channel("lexical", started_at=started_at)
        return _merge_channel_results(
            "lexical",
            results,
            candidate_limit=candidate_limit,
            started_at=started_at,
        )

    def _retrieve_user_semantic(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        started_at = perf_counter()
        scope = request.source_scope
        assert scope is not None and scope.owner_principal_id is not None
        try:
            if self._embedding_provider is None:
                raise RuntimeError("embedding provider is unavailable")
            query_embedding = tuple(
                float(value)
                for value in self._embedding_provider.embed_query(
                    request.query_text
                )
            )
            chunks = self._user_chunks_factory().search_semantic(
                owner_principal_id=scope.owner_principal_id,
                allowed_document_revision_ids=(
                    scope.allowed_document_revision_ids
                ),
                query_embedding=query_embedding,
                limit=candidate_limit,
            )
            selected = scope.selected_document_by_revision_id
            scored = []
            for chunk in chunks:
                frozen = selected.get(chunk.document_revision_id)
                if frozen is None or chunk.document_id != frozen.document_id:
                    continue
                score = _cosine_similarity(query_embedding, chunk.embedding)
                scored.append((score, _user_knowledge_chunk(chunk, frozen)))
            scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
            candidates = [
                RetrievalCandidate(
                    chunk=chunk.model_copy(update={"score": score}),
                    semantic_score=score,
                    semantic_rank=rank,
                    channel_hits=["semantic"],
                )
                for rank, (score, chunk) in enumerate(
                    scored[:candidate_limit],
                    start=1,
                )
            ]
        except Exception:
            return _unavailable_channel(
                "semantic",
                "user_material_semantic_unavailable",
                started_at=started_at,
            )
        return _completed_channel(
            "semantic",
            candidates,
            started_at=started_at,
        )

    def _retrieve_user_lexical(
        self,
        request: RetrievalRequest,
        *,
        candidate_limit: int,
    ) -> RetrievalChannelResult:
        started_at = perf_counter()
        scope = request.source_scope
        assert scope is not None and scope.owner_principal_id is not None
        try:
            chunks = self._user_chunks_factory().search_lexical(
                owner_principal_id=scope.owner_principal_id,
                allowed_document_revision_ids=(
                    scope.allowed_document_revision_ids
                ),
                query_text=request.query_text,
                limit=candidate_limit,
            )
            selected = scope.selected_document_by_revision_id
            query_terms = extract_technical_terms(request.query_text)
            scored = []
            for chunk in chunks:
                frozen = selected.get(chunk.document_revision_id)
                if frozen is None or chunk.document_id != frozen.document_id:
                    continue
                mapped = _user_knowledge_chunk(chunk, frozen)
                score, matched = lexical_match_score(query_terms, mapped)
                if score > 0:
                    scored.append((score, mapped, matched))
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
                    scored[:candidate_limit],
                    start=1,
                )
            ]
        except Exception:
            return _unavailable_channel(
                "lexical",
                "user_material_lexical_unavailable",
                started_at=started_at,
            )
        return _completed_channel(
            "lexical",
            candidates,
            started_at=started_at,
        )


def _merge_channel_results(
    channel: str,
    results: list[RetrievalChannelResult],
    *,
    candidate_limit: int,
    started_at: float,
) -> RetrievalChannelResult:
    available = [
        item
        for item in results
        if item.availability != RetrievalAvailability.UNAVAILABLE
    ]
    if not available:
        availability = RetrievalAvailability.UNAVAILABLE
    elif len(available) != len(results) or any(
        item.availability == RetrievalAvailability.DEGRADED
        for item in available
    ):
        availability = RetrievalAvailability.DEGRADED
    else:
        availability = RetrievalAvailability.AVAILABLE
    candidates_by_id = {}
    for result in available:
        for candidate in result.candidates:
            current = candidates_by_id.get(candidate.chunk_id)
            if current is None or _channel_score(candidate, channel) > _channel_score(
                current,
                channel,
            ):
                candidates_by_id[candidate.chunk_id] = candidate
    ranked = sorted(
        candidates_by_id.values(),
        key=lambda item: (-_channel_score(item, channel), item.chunk_id),
    )[: max(1, int(candidate_limit))]
    candidates = [
        candidate.model_copy(
            update={
                f"{channel}_rank": rank,
                "channel_hits": [channel],
            }
        )
        for rank, candidate in enumerate(ranked, start=1)
    ]
    non_available_reason = next(
        (
            item.trace.reason_code
            for item in results
            if item.availability != RetrievalAvailability.AVAILABLE
            and item.trace.reason_code
        ),
        None,
    )
    system_result = next(
        (
            item
            for item in results
            if item.corpus_version or item.corpus_manifest_sha256
        ),
        None,
    )
    latency = round((perf_counter() - started_at) * 1000, 3)
    return RetrievalChannelResult(
        availability=availability,
        candidates=candidates,
        trace=RetrievalChannelTrace(
            channel=channel,
            status=(
                "unavailable"
                if availability == RetrievalAvailability.UNAVAILABLE
                else "completed" if candidates else "empty"
            ),
            latency_ms=latency,
            candidate_count=len(candidates),
            hit_ids=[item.chunk_id for item in candidates],
            reason_code=non_available_reason,
        ),
        corpus_version=(system_result.corpus_version if system_result else None),
        corpus_manifest_sha256=(
            system_result.corpus_manifest_sha256 if system_result else None
        ),
    )


def _channel_score(candidate: RetrievalCandidate, channel: str) -> float:
    value = (
        candidate.semantic_score
        if channel == "semantic"
        else candidate.lexical_score
    )
    return float(value if value is not None else candidate.chunk.score or 0.0)


def _user_knowledge_chunk(chunk, frozen) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk.chunk_id,
        title=chunk.title,
        content=chunk.content,
        source_type="user_material",
        domain="user_material",
        tags=[],
        metadata={
            "knowledge_source": "user_material",
            "document_id": chunk.document_id,
            "document_revision_id": chunk.document_revision_id,
            "content_sha256": chunk.content_sha256,
            "document_content_sha256": frozen.content_sha256,
            "provenance": {
                "knowledge_source": "user_material",
                "document_id": chunk.document_id,
                "document_revision_id": chunk.document_revision_id,
                "document_content_sha256": frozen.content_sha256,
            },
            **(
                {"section_label": chunk.section_label}
                if chunk.section_label
                else {}
            ),
        },
    )


def _cosine_similarity(left, right) -> float:
    if not left or len(left) != len(right):
        raise ValueError("embedding dimension mismatch")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _empty_channel(channel: str, *, started_at: float) -> RetrievalChannelResult:
    return _completed_channel(channel, [], started_at=started_at)


def _completed_channel(
    channel: str,
    candidates: list[RetrievalCandidate],
    *,
    started_at: float,
) -> RetrievalChannelResult:
    return RetrievalChannelResult(
        availability=RetrievalAvailability.AVAILABLE,
        candidates=candidates,
        trace=RetrievalChannelTrace(
            channel=channel,
            status="completed" if candidates else "empty",
            latency_ms=round((perf_counter() - started_at) * 1000, 3),
            candidate_count=len(candidates),
            hit_ids=[item.chunk_id for item in candidates],
        ),
    )


def _unavailable_channel(
    channel: str,
    reason_code: str,
    *,
    started_at: float,
) -> RetrievalChannelResult:
    return RetrievalChannelResult(
        availability=RetrievalAvailability.UNAVAILABLE,
        trace=RetrievalChannelTrace(
            channel=channel,
            status="unavailable",
            latency_ms=round((perf_counter() - started_at) * 1000, 3),
            candidate_count=0,
            reason_code=reason_code,
        ),
    )


__all__ = ["SourceAwareKnowledgeRetriever"]
