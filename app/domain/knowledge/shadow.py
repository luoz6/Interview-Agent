from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.knowledge.retrieval import RetrievalResult
from app.domain.knowledge.evidence import EvidenceDecision


class ShadowEngineSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_version: str
    profile_version: str
    candidate_ids: tuple[str, ...] = ()
    selected_evidence_ids: tuple[str, ...] = ()
    availability: str
    reason_codes: tuple[str, ...] = ()
    gate_reason_codes: tuple[str, ...] = ()
    channel_latency_ms: dict[str, float] = Field(default_factory=dict)
    latency_ms: float = Field(ge=0)


class RetrievalShadowComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    legacy: ShadowEngineSummary
    candidate: ShadowEngineSummary
    selected_evidence_changed: bool
    availability_changed: bool
    gate_changed: bool
    shadow_compute_latency_ms: float = Field(ge=0)
    shadow_overhead_latency_ms: float = Field(ge=0)
    shadow_version: str = "retrieval-shadow-v1"


class RetrievalShadowFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    legacy: ShadowEngineSummary
    candidate_engine_version: str
    shadow_compute_latency_ms: float = Field(ge=0)
    shadow_overhead_latency_ms: float = Field(ge=0)
    reason_code: str = "shadow_candidate_failed"
    shadow_version: str = "retrieval-shadow-v1"


def summarize_shadow_result(
    result: RetrievalResult,
    gate: EvidenceDecision | None = None,
) -> ShadowEngineSummary:
    resolved_gate = gate or result.evidence_decision
    ranked = sorted(
        result.candidates,
        key=lambda item: (
            item.rerank_rank or item.fusion_rank or item.semantic_rank or 10**9,
            item.chunk_id,
        ),
    )
    return ShadowEngineSummary(
        engine_version=result.retrieval_engine_version,
        profile_version=result.profile_version,
        candidate_ids=tuple(item.chunk_id for item in ranked),
        selected_evidence_ids=tuple(item.chunk_id for item in result.selected_evidence),
        availability=result.availability.value,
        reason_codes=tuple(result.degraded_reasons),
        gate_reason_codes=(
            tuple(resolved_gate.reason_codes) if resolved_gate is not None else ()
        ),
        channel_latency_ms={
            channel.channel: channel.latency_ms for channel in result.trace.channels
        },
        latency_ms=result.latency_ms,
    )
