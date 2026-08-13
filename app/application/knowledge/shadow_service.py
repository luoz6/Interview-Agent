from __future__ import annotations

from hashlib import sha256
from time import perf_counter

from app.domain.knowledge.shadow import (
    RetrievalShadowComparison,
    RetrievalShadowFailure,
    summarize_shadow_result,
)


class RetrievalShadowService:
    """Runs compare-only retrieval and never receives a binding writer."""

    def __init__(self, legacy_engine, candidate_engine) -> None:
        self.legacy_engine = legacy_engine
        self.candidate_engine = candidate_engine

    def compare(
        self,
        request,
        *,
        legacy_profile,
        candidate_profile,
        legacy_gate=None,
        candidate_gate=None,
    ):
        legacy_result = self.legacy_engine.retrieve(request, legacy_profile)
        return self.compare_with_legacy(
            request,
            legacy_result=legacy_result,
            candidate_profile=candidate_profile,
            legacy_gate=legacy_gate,
            candidate_gate=candidate_gate,
        )

    def compare_with_legacy(
        self,
        request,
        *,
        legacy_result,
        candidate_profile,
        legacy_gate=None,
        candidate_gate=None,
    ):
        legacy = summarize_shadow_result(legacy_result, legacy_gate)
        query_sha256 = sha256(request.query_text.encode("utf-8")).hexdigest()
        shadow_started_at = perf_counter()
        try:
            candidate_result = self.candidate_engine.retrieve(
                request, candidate_profile
            )
        except Exception:
            shadow_latency = round((perf_counter() - shadow_started_at) * 1000, 3)
            return legacy_result, RetrievalShadowFailure(
                request_id=request.request_id,
                query_sha256=query_sha256,
                legacy=legacy,
                candidate_engine_version=str(
                    getattr(self.candidate_engine, "ENGINE_VERSION", "unknown")
                ),
                shadow_compute_latency_ms=shadow_latency,
                shadow_overhead_latency_ms=0,
            )
        candidate = summarize_shadow_result(candidate_result, candidate_gate)
        shadow_latency = round((perf_counter() - shadow_started_at) * 1000, 3)
        comparison = RetrievalShadowComparison(
            request_id=request.request_id,
            query_sha256=query_sha256,
            legacy=legacy,
            candidate=candidate,
            selected_evidence_changed=(
                legacy.selected_evidence_ids != candidate.selected_evidence_ids
            ),
            availability_changed=legacy.availability != candidate.availability,
            gate_changed=legacy.gate_reason_codes != candidate.gate_reason_codes,
            shadow_compute_latency_ms=candidate.latency_ms,
            shadow_overhead_latency_ms=max(
                0.0, round(shadow_latency - candidate.latency_ms, 3)
            ),
        )
        return legacy_result, comparison
