from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from threading import Lock
from time import perf_counter

from app.domain.knowledge.evidence_gate import RetrievalEvidenceGate
from app.domain.knowledge.fusion import fuse_retrieval_candidates
from app.domain.knowledge.reranking import KnowledgeReranker
from app.domain.knowledge.retrieval import (
    RetrievalFusionSummary,
    RetrievalRerankSummary,
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalRequest,
    RetrievalResult,
    RetrievalChannelResult,
    RetrievalChannelTrace,
    build_retrieval_trace,
)
from app.ports.knowledge import LexicalRetrieverPort, SemanticRetrieverPort


class HybridKnowledgeRetrievalService:
    ENGINE_VERSION = "hybrid-v2"

    def __init__(
        self,
        semantic_retriever: SemanticRetrieverPort,
        lexical_retriever: LexicalRetrieverPort,
        *,
        reranker: KnowledgeReranker | None = None,
        component_versions: dict[str, str] | None = None,
        evidence_gate: RetrievalEvidenceGate | None = None,
    ) -> None:
        self._semantic = semantic_retriever
        self._lexical = lexical_retriever
        self._reranker = reranker or KnowledgeReranker()
        self._component_versions = dict(component_versions or {})
        self._evidence_gate = evidence_gate or RetrievalEvidenceGate()
        self._executor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="knowledge-hybrid-channel",
        )
        self._inflight_lock = Lock()
        self._inflight: dict[str, Future] = {}

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def retrieve(
        self,
        request: RetrievalRequest,
        profile: ResolvedRetrievalProfile,
    ) -> RetrievalResult:
        started_at = perf_counter()
        futures: dict[str, Future] = {}
        saturated: set[str] = set()
        if profile.semantic_enabled:
            future = self._submit_channel(
                "semantic",
                self._semantic.retrieve_semantic,
                request,
                candidate_limit=profile.semantic_candidate_limit,
            )
            if future is None:
                saturated.add("semantic")
            else:
                futures["semantic"] = future
        if profile.lexical_enabled:
            future = self._submit_channel(
                "lexical",
                self._lexical.retrieve_lexical,
                request,
                candidate_limit=profile.lexical_candidate_limit,
            )
            if future is None:
                saturated.add("lexical")
            else:
                futures["lexical"] = future
        semantic = self._resolve_channel(
            "semantic",
            futures.get("semantic"),
            enabled=profile.semantic_enabled,
            saturated="semantic" in saturated,
            started_at=started_at,
            channel_timeout_ms=profile.semantic_timeout_ms,
            total_timeout_ms=profile.total_timeout_ms,
        )
        lexical = self._resolve_channel(
            "lexical",
            futures.get("lexical"),
            enabled=profile.lexical_enabled,
            saturated="lexical" in saturated,
            started_at=started_at,
            channel_timeout_ms=profile.lexical_timeout_ms,
            total_timeout_ms=profile.total_timeout_ms,
        )
        channel_results = [item for item in (semantic, lexical) if item is not None]
        corpus_versions = {
            item.corpus_version for item in channel_results if item.corpus_version
        }
        corpus_manifests = {
            item.corpus_manifest_sha256
            for item in channel_results
            if item.corpus_manifest_sha256
        }
        available = [
            item
            for item in channel_results
            if item.availability != RetrievalAvailability.UNAVAILABLE
        ]
        degraded_reasons = [
            item.trace.reason_code
            for item in channel_results
            if item.trace.reason_code
        ]
        if not available:
            availability = RetrievalAvailability.UNAVAILABLE
        elif len(available) != len(channel_results):
            availability = RetrievalAvailability.DEGRADED
        else:
            availability = RetrievalAvailability.AVAILABLE

        fused = fuse_retrieval_candidates(
            semantic.candidates if semantic in available else [],
            lexical.candidates if lexical in available else [],
            strategy=profile.fusion_strategy,
            k=profile.rrf_k,
            semantic_weight=profile.semantic_weight,
            lexical_weight=profile.lexical_weight,
            limit=profile.fusion_candidate_limit,
        )
        rerank_candidates = fused[: profile.rerank_candidate_limit]
        ranked_chunks, reranker_reason = self._run_reranker(
            rerank_candidates,
            request=request,
            profile=profile,
            started_at=started_at,
        )
        if reranker_reason:
            degraded_reasons.append(reranker_reason)
            if availability == RetrievalAvailability.AVAILABLE:
                availability = RetrievalAvailability.DEGRADED
        rank_by_id = {
            chunk.chunk_id: rank for rank, chunk in enumerate(ranked_chunks, 1)
        }
        score_by_id = {
            chunk.chunk_id: float(chunk.score or 0.0) for chunk in ranked_chunks
        }
        fused = [
            item.model_copy(
                update={
                    "rerank_rank": rank_by_id.get(item.chunk_id),
                    "rerank_score": score_by_id.get(item.chunk_id),
                }
            )
            for item in fused
        ]
        latency = round((perf_counter() - started_at) * 1000, 3)
        evidence_decision = self._evidence_gate.decide_selection(
            availability,
            ranked_chunks,
        )
        return RetrievalResult(
            request_id=request.request_id,
            availability=availability,
            candidates=fused,
            selected_evidence=ranked_chunks,
            evidence_decision=evidence_decision,
            trace=build_retrieval_trace(
                request=request,
                profile=profile,
                channels=[item.trace for item in channel_results],
                selected_evidence=ranked_chunks,
                degraded_reasons=degraded_reasons,
                latency_ms=latency,
                latency_breakdown_ms={
                    **{
                        item.trace.channel: item.trace.latency_ms
                        for item in channel_results
                    },
                    "fusion_rerank_and_selection": max(
                        0.0,
                        latency
                        - max(
                            (item.trace.latency_ms for item in channel_results),
                            default=0.0,
                        ),
                    ),
                    "total": latency,
                },
                fusion_summary=RetrievalFusionSummary(
                    strategy=profile.fusion_strategy,
                    semantic_candidate_count=len(
                        semantic.candidates if semantic in available else []
                    ),
                    lexical_candidate_count=len(
                        lexical.candidates if lexical in available else []
                    ),
                    fused_candidate_count=len(fused),
                    candidate_limit=profile.fusion_candidate_limit,
                    rrf_k=profile.rrf_k,
                    semantic_weight=profile.semantic_weight,
                    lexical_weight=profile.lexical_weight,
                ),
                evidence_decision=evidence_decision,
                rerank_summary=RetrievalRerankSummary(
                    strategy_version=self._component_versions.get(
                        "reranker_version", "deterministic-v1"
                    ),
                    input_candidate_count=len(rerank_candidates),
                    selected_count=len(ranked_chunks),
                    candidate_limit=profile.rerank_candidate_limit,
                    evidence_limit=profile.evidence_limit,
                    minimum_score=profile.minimum_score,
                ),
                component_versions={
                    **self._component_versions,
                    "retrieval_engine_version": self.ENGINE_VERSION,
                    "corpus_version": (
                        next(iter(corpus_versions))
                        if len(corpus_versions) == 1
                        else ""
                    ),
                    "corpus_manifest_sha256": (
                        next(iter(corpus_manifests))
                        if len(corpus_manifests) == 1
                        else ""
                    ),
                },
            ),
            retrieval_engine_version=self.ENGINE_VERSION,
            profile_version=profile.profile_version,
            latency_ms=latency,
            degraded_reasons=degraded_reasons,
        )

    def _submit_channel(self, channel: str, function, *args, **kwargs):
        with self._inflight_lock:
            existing = self._inflight.get(channel)
            if existing is not None and not existing.done():
                return None
            future = self._executor.submit(function, *args, **kwargs)
            self._inflight[channel] = future
            return future

    def _run_reranker(
        self,
        candidates,
        *,
        request: RetrievalRequest,
        profile: ResolvedRetrievalProfile,
        started_at: float,
    ):
        chunks = [item.chunk for item in candidates]
        future = self._submit_channel(
            "reranker",
            self._reranker.rerank,
            chunks,
            query_text=request.query_text,
            requested_tags=list(
                request.routing_hints.domains
                + request.routing_hints.canonical_tags
            ),
            minimum_score=profile.minimum_score,
            limit=profile.evidence_limit,
        )
        if future is None:
            return self._fusion_fallback(candidates, profile), "reranker_capacity_exhausted"
        elapsed_ms = (perf_counter() - started_at) * 1000
        remaining_ms = min(profile.rerank_timeout_ms, profile.total_timeout_ms - elapsed_ms)
        try:
            if remaining_ms <= 0 and not future.done():
                raise TimeoutError
            return future.result(timeout=max(0, remaining_ms) / 1000), None
        except TimeoutError:
            future.cancel()
            return self._fusion_fallback(candidates, profile), "reranker_timeout"
        except Exception:
            return self._fusion_fallback(candidates, profile), "reranker_provider_error"

    @staticmethod
    def _fusion_fallback(candidates, profile):
        return [
            item.chunk
            for item in candidates
            if float(item.chunk.score or 0.0) >= profile.minimum_score
        ][: profile.evidence_limit]

    @staticmethod
    def _resolve_channel(
        channel: str,
        future: Future | None,
        *,
        enabled: bool,
        saturated: bool,
        started_at: float,
        channel_timeout_ms: int,
        total_timeout_ms: int,
    ) -> RetrievalChannelResult | None:
        if not enabled:
            return None
        if future is None:
            return RetrievalChannelResult(
                availability=RetrievalAvailability.UNAVAILABLE,
                trace=RetrievalChannelTrace(
                    channel=channel,
                    status="unavailable",
                    latency_ms=round((perf_counter() - started_at) * 1000, 3),
                    candidate_count=0,
                    reason_code=(
                        f"{channel}_capacity_exhausted"
                        if saturated
                        else f"{channel}_provider_error"
                    ),
                ),
            )
        elapsed_ms = (perf_counter() - started_at) * 1000
        remaining_ms = min(channel_timeout_ms, total_timeout_ms) - elapsed_ms
        try:
            if remaining_ms <= 0 and not future.done():
                raise TimeoutError
            return future.result(timeout=max(0, remaining_ms) / 1000)
        except TimeoutError:
            future.cancel()
            reason_code = f"{channel}_timeout"
        except Exception:
            reason_code = f"{channel}_provider_error"
        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        return RetrievalChannelResult(
            availability=RetrievalAvailability.UNAVAILABLE,
            trace=RetrievalChannelTrace(
                channel=channel,
                status="unavailable",
                latency_ms=latency_ms,
                candidate_count=0,
                reason_code=reason_code,
            ),
        )
