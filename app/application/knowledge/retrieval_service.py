from __future__ import annotations

from time import perf_counter

from app.domain.knowledge.evidence_gate import RetrievalEvidenceGate
from app.domain.knowledge.reranking import KnowledgeReranker
from app.domain.knowledge.retrieval import (
    RetrievalRerankSummary,
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResult,
    build_retrieval_trace,
)
from app.ports.knowledge import SemanticRetrieverPort


class KnowledgeRetrievalService:
    """Application policy for retrieval, ranking, and final evidence selection."""

    ENGINE_VERSION = "compatibility-v1"

    def __init__(
        self,
        semantic_retriever: SemanticRetrieverPort,
        *,
        reranker: KnowledgeReranker | None = None,
        engine_version: str | None = None,
        component_versions: dict[str, str] | None = None,
        evidence_gate: RetrievalEvidenceGate | None = None,
    ) -> None:
        self._semantic_retriever = semantic_retriever
        self._reranker = reranker or KnowledgeReranker()
        self._engine_version = engine_version or self.ENGINE_VERSION
        self._component_versions = dict(component_versions or {})
        self._evidence_gate = evidence_gate or RetrievalEvidenceGate()

    def retrieve(
        self,
        request: RetrievalRequest,
        profile: ResolvedRetrievalProfile,
    ) -> RetrievalResult:
        started_at = perf_counter()
        channel = self._semantic_retriever.retrieve_semantic(
            request,
            candidate_limit=profile.semantic_candidate_limit,
        )
        raw_chunks = [candidate.chunk for candidate in channel.candidates]
        rerank_started_at = perf_counter()
        ranked = self._reranker.rerank(
            raw_chunks,
            query_text=request.query_text,
            requested_tags=list(
                request.routing_hints.domains
                + request.routing_hints.canonical_tags
            ),
            minimum_score=profile.minimum_score,
            limit=profile.evidence_limit,
        )
        rerank_ms = round((perf_counter() - rerank_started_at) * 1000, 3)
        scores = {chunk.chunk_id: float(chunk.score or 0.0) for chunk in ranked}
        rank_by_id = {chunk.chunk_id: rank for rank, chunk in enumerate(ranked, 1)}
        candidates = [
            candidate.model_copy(
                update={
                    "rerank_score": scores.get(candidate.chunk_id),
                    "rerank_rank": rank_by_id.get(candidate.chunk_id),
                }
            )
            for candidate in channel.candidates
        ]
        degraded_reasons = (
            [channel.trace.reason_code] if channel.trace.reason_code else []
        )
        evidence_gate_started_at = perf_counter()
        evidence_decision = self._evidence_gate.decide_selection(
            channel.availability,
            ranked,
        )
        evidence_gate_ms = round(
            (perf_counter() - evidence_gate_started_at) * 1000, 3
        )
        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        trace = build_retrieval_trace(
            request=request,
            profile=profile,
            channels=[channel.trace],
            selected_evidence=ranked,
            degraded_reasons=degraded_reasons,
            latency_ms=latency_ms,
            latency_breakdown_ms={
                "semantic": channel.trace.latency_ms,
                "lexical": None,
                "fusion": None,
                "rerank": rerank_ms,
                "evidence_gate": evidence_gate_ms,
                "total": latency_ms,
            },
            fusion_summary=None,
            evidence_decision=evidence_decision,
            rerank_summary=RetrievalRerankSummary(
                strategy_version=self._component_versions.get(
                    "reranker_version", "deterministic-v1"
                ),
                input_candidate_count=len(raw_chunks),
                selected_count=len(ranked),
                candidate_limit=profile.rerank_candidate_limit,
                evidence_limit=profile.evidence_limit,
                minimum_score=profile.minimum_score,
            ),
            component_versions={
                **self._component_versions,
                "retrieval_engine_version": self._engine_version,
                "corpus_version": channel.corpus_version or "",
                "corpus_manifest_sha256": (
                    channel.corpus_manifest_sha256 or ""
                ),
            },
        )
        return RetrievalResult(
            request_id=request.request_id,
            availability=channel.availability,
            candidates=candidates,
            selected_evidence=ranked,
            evidence_decision=evidence_decision,
            trace=trace,
            retrieval_engine_version=self._engine_version,
            profile_version=profile.profile_version,
            latency_ms=latency_ms,
            degraded_reasons=degraded_reasons,
        )
