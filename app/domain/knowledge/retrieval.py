from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.knowledge.evidence import EvidenceDecision
from app.domain.knowledge.models import KnowledgeChunk


class RetrievalIntent(StrEnum):
    PREP = "prep"
    FOLLOWUP = "followup"
    QUESTION_REVIEW = "question_review"
    REPORT_REPAIR = "report_repair"
    EVAL = "eval"


class RetrievalAvailability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class RetrievalReasonCode(StrEnum):
    """Stable cross-layer reason codes required by the RAG V2 contract."""

    SEMANTIC_TIMEOUT = "semantic_timeout"
    LEXICAL_TIMEOUT = "lexical_timeout"
    SEMANTIC_PROVIDER_ERROR = "semantic_provider_error"
    LEXICAL_PROVIDER_ERROR = "lexical_provider_error"
    SEMANTIC_CAPACITY_EXHAUSTED = "semantic_capacity_exhausted"
    LEXICAL_CAPACITY_EXHAUSTED = "lexical_capacity_exhausted"
    EMBEDDING_PROVIDER_ERROR = "embedding_provider_error"
    RERANKER_TIMEOUT = "reranker_timeout"
    RERANKER_PROVIDER_ERROR = "reranker_provider_error"
    CANDIDATE_ENGINE_FAILED = "candidate_engine_failed"
    INVALID_KNOWLEDGE_METADATA = "invalid_knowledge_metadata"
    CORPUS_MANIFEST_MISMATCH = "corpus_manifest_mismatch"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
    NO_RELEVANT_CANDIDATE = "no_relevant_candidate"
    INSUFFICIENT_SIGNAL_COVERAGE = "insufficient_signal_coverage"
    HARD_NEGATIVE_RISK = "hard_negative_risk"
    SUPPLEMENTAL_RETRIEVAL_REQUIRED = "supplemental_retrieval_required"
    SUPPLEMENTAL_RETRIEVAL_UNAVAILABLE = "supplemental_retrieval_unavailable"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    EVIDENCE_GATE_DISABLED = "evidence_gate_disabled"
    KNOWLEDGE_UNIT_NOT_REVIEWED = "knowledge_unit_not_reviewed"
    EVIDENCE_TASK_MISMATCH = "evidence_task_mismatch"
    EVIDENCE_AUTHORITY_UNVERIFIED = "evidence_authority_unverified"
    EVIDENCE_AUTHORITY_FILTERED = "evidence_authority_filtered"


class RetrievalHardConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_types: tuple[str, ...] = ()
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value):
        supported = {"tags", "domains", "include_general_tag"}
        unknown = sorted(set(value) - supported)
        if unknown:
            raise ValueError(
                "unsupported hard retrieval filters: " + ", ".join(unknown)
            )
        return value


class RetrievalRoutingHints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = ""
    seniority: str = ""
    domains: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    canonical_tags: tuple[str, ...] = ()


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(default_factory=lambda: f"retrieval-{uuid4().hex}")
    query_text: str = Field(min_length=1, max_length=4000)
    intent: RetrievalIntent
    hard_constraints: RetrievalHardConstraints = Field(
        default_factory=RetrievalHardConstraints
    )
    routing_hints: RetrievalRoutingHints = Field(default_factory=RetrievalRoutingHints)
    profile_id: str
    session_id: str | None = None
    question_id: str | None = None
    prep_run_id: str | None = None
    parent_bundle_id: str | None = None


class ResolvedRetrievalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    profile_version: str
    semantic_enabled: bool = True
    lexical_enabled: bool = False
    remote_reranker_enabled: bool = False
    semantic_candidate_limit: int = Field(default=12, ge=1, le=200)
    lexical_candidate_limit: int = Field(default=12, ge=1, le=200)
    fusion_candidate_limit: int = Field(default=12, ge=1, le=200)
    rerank_candidate_limit: int = Field(default=12, ge=1, le=200)
    evidence_limit: int = Field(default=5, ge=1, le=50)
    minimum_score: float = Field(default=0.0, ge=0.0, le=1.0)
    fusion_strategy: Literal["weighted_rrf", "rank_normalized_score"] = (
        "weighted_rrf"
    )
    routing_policy: Literal["soft", "hard"] = "soft"
    rrf_k: int = Field(default=60, ge=1, le=1000)
    semantic_weight: float = Field(default=1.0, gt=0)
    lexical_weight: float = Field(default=1.0, gt=0)
    query_aware_fusion: bool = False
    semantic_timeout_ms: int = Field(default=1500, ge=1)
    lexical_timeout_ms: int = Field(default=500, ge=1)
    rerank_timeout_ms: int = Field(default=1500, ge=1)
    total_timeout_ms: int = Field(default=3000, ge=1)


class RetrievalChannelTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str
    status: str
    latency_ms: float = Field(ge=0)
    candidate_count: int = Field(ge=0)
    hit_ids: list[str] = Field(default_factory=list)
    reason_code: str | None = None


class CandidateRankingExplanation(BaseModel):
    """Auditable values emitted by the ranking path that used them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_score_source: Literal[
        "fusion_score", "semantic_score", "lexical_score", "chunk_score"
    ]
    base_score: float
    exact_term_boost: float = 0.0
    routing_tag_boost: float = 0.0
    eligibility_score: float
    eligible: bool
    final_rerank_score: float | None = None
    tie_break_fusion_rank: int | None = Field(default=None, ge=1)
    reason_codes: tuple[str, ...] = ()


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk: KnowledgeChunk
    semantic_score: float | None = None
    lexical_score: float | None = None
    semantic_rank: int | None = Field(default=None, ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    fusion_score: float | None = None
    fusion_rank: int | None = Field(default=None, ge=1)
    rerank_score: float | None = None
    rerank_rank: int | None = Field(default=None, ge=1)
    channel_hits: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    ranking_explanation: CandidateRankingExplanation | None = None

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id


class SanitizedRetrievalQueryFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    character_count: int = Field(ge=1, le=4000)


class RetrievalConstraintSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type_count: int = Field(default=0, ge=0)
    filter_keys: tuple[str, ...] = ()
    values_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RetrievalRoutingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role_present: bool = False
    seniority_present: bool = False
    domain_count: int = Field(default=0, ge=0)
    topic_count: int = Field(default=0, ge=0)
    canonical_tag_count: int = Field(default=0, ge=0)
    values_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RetrievalFusionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: str
    semantic_candidate_count: int = Field(default=0, ge=0)
    lexical_candidate_count: int = Field(default=0, ge=0)
    fused_candidate_count: int = Field(default=0, ge=0)
    candidate_limit: int = Field(ge=1)
    rrf_k: int = Field(ge=1)
    semantic_weight: float = Field(gt=0)
    lexical_weight: float = Field(gt=0)
    query_signal: Literal[
        "lexical_dominant",
        "semantic_dominant",
        "balanced",
    ] = "balanced"
    reason_codes: tuple[str, ...] = ()


class RetrievalRerankSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_version: str
    input_candidate_count: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    candidate_limit: int = Field(ge=1)
    evidence_limit: int = Field(ge=1)
    minimum_score: float = Field(ge=0, le=1)


class RetrievalComponentVersions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_version: str = ""
    corpus_manifest_sha256: str = ""
    embedding_provider: str = ""
    embedding_model: str = ""
    model_revision: str = ""
    retrieval_engine_version: str
    profile_version: str
    fusion_version: str
    reranker_version: str
    evidence_gate_version: str = ""
    taxonomy_version: str = ""
    knowledge_unit_schema_version: str = "knowledge-unit-v2"


class RetrievalTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    trace_schema_version: Literal["retrieval-trace-v2", "retrieval-trace-v3"] = (
        "retrieval-trace-v3"
    )
    intent: RetrievalIntent | None = None
    profile_id: str
    profile_version: str
    sanitized_query_facts: SanitizedRetrievalQueryFacts | None = None
    resolved_profile: ResolvedRetrievalProfile | None = None
    hard_constraints: RetrievalConstraintSummary | None = None
    routing_hints: RetrievalRoutingSummary | None = None
    channels: list[RetrievalChannelTrace] = Field(default_factory=list)
    fusion_summary: RetrievalFusionSummary | None = None
    rerank_summary: RetrievalRerankSummary | None = None
    evidence_decision: EvidenceDecision | None = None
    latency_ms: float = Field(ge=0)
    latency_breakdown_ms: dict[str, float | None] = Field(default_factory=dict)
    selected_evidence_ids: list[str] = Field(default_factory=list)
    selected_evidence_hashes: dict[str, str] = Field(default_factory=dict)
    degraded_path_latency_ms: float | None = Field(default=None, ge=0)
    degraded_path: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    component_versions: RetrievalComponentVersions | None = None


class RetrievalChannelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: RetrievalAvailability
    candidates: list[RetrievalCandidate] = Field(default_factory=list)
    trace: RetrievalChannelTrace
    corpus_version: str | None = None
    corpus_manifest_sha256: str | None = None


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    availability: RetrievalAvailability
    candidates: list[RetrievalCandidate] = Field(default_factory=list)
    selected_evidence: list[KnowledgeChunk] = Field(default_factory=list)
    evidence_decision: EvidenceDecision | None = None
    trace: RetrievalTrace
    retrieval_engine_version: str
    profile_version: str
    latency_ms: float = Field(ge=0)
    degraded_reasons: list[str] = Field(default_factory=list)


def build_retrieval_trace(
    *,
    request: RetrievalRequest,
    profile: ResolvedRetrievalProfile,
    channels: list[RetrievalChannelTrace],
    selected_evidence: list[KnowledgeChunk],
    degraded_reasons: list[str],
    latency_ms: float,
    latency_breakdown_ms: dict[str, float | None],
    fusion_summary: RetrievalFusionSummary | None,
    rerank_summary: RetrievalRerankSummary,
    evidence_decision: EvidenceDecision,
    component_versions: dict[str, str] | None = None,
) -> RetrievalTrace:
    """Build one privacy-safe V2 trace for both Legacy and Hybrid engines."""

    constraint_payload = request.hard_constraints.model_dump(mode="json")
    routing_payload = request.routing_hints.model_dump(mode="json")
    selected_hashes = {
        chunk.chunk_id: str(chunk.metadata.get("content_sha256") or "")
        for chunk in selected_evidence
        if chunk.metadata.get("content_sha256")
    }
    versions = dict(component_versions or {})
    versions.setdefault("profile_version", profile.profile_version)
    versions.setdefault("fusion_version", profile.fusion_strategy)
    versions.setdefault("reranker_version", "deterministic-v1")
    versions.setdefault("retrieval_engine_version", "unknown")
    corpus_versions = {
        str(chunk.metadata.get("corpus_version") or "")
        for chunk in selected_evidence
        if chunk.metadata.get("corpus_version")
    }
    manifests = {
        str(chunk.metadata.get("corpus_manifest_sha256") or "")
        for chunk in selected_evidence
        if chunk.metadata.get("corpus_manifest_sha256")
    }
    versions.setdefault(
        "corpus_version", next(iter(corpus_versions)) if len(corpus_versions) == 1 else ""
    )
    versions.setdefault(
        "corpus_manifest_sha256", next(iter(manifests)) if len(manifests) == 1 else ""
    )
    versions.setdefault("embedding_provider", "")
    versions.setdefault("embedding_model", "")
    versions.setdefault("model_revision", "")
    versions.setdefault("evidence_gate_version", "")
    versions.setdefault("taxonomy_version", "")
    versions.setdefault("knowledge_unit_schema_version", "knowledge-unit-v2")
    return RetrievalTrace(
        request_id=request.request_id,
        intent=request.intent,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        resolved_profile=profile,
        sanitized_query_facts=SanitizedRetrievalQueryFacts(
            query_sha256=hashlib.sha256(request.query_text.encode("utf-8")).hexdigest(),
            character_count=len(request.query_text),
        ),
        hard_constraints=RetrievalConstraintSummary(
            source_type_count=len(request.hard_constraints.source_types),
            filter_keys=tuple(sorted(request.hard_constraints.filters)),
            values_sha256=_trace_sha256(constraint_payload),
        ),
        routing_hints=RetrievalRoutingSummary(
            role_present=bool(request.routing_hints.role),
            seniority_present=bool(request.routing_hints.seniority),
            domain_count=len(request.routing_hints.domains),
            topic_count=len(request.routing_hints.topics),
            canonical_tag_count=len(request.routing_hints.canonical_tags),
            values_sha256=_trace_sha256(routing_payload),
        ),
        channels=channels,
        fusion_summary=fusion_summary,
        rerank_summary=rerank_summary,
        evidence_decision=evidence_decision,
        latency_ms=latency_ms,
        latency_breakdown_ms=latency_breakdown_ms,
        selected_evidence_ids=[chunk.chunk_id for chunk in selected_evidence],
        selected_evidence_hashes=selected_hashes,
        degraded_path_latency_ms=latency_ms if degraded_reasons else None,
        degraded_path=list(degraded_reasons),
        degraded_reasons=list(degraded_reasons),
        component_versions=RetrievalComponentVersions.model_validate(versions),
    )


def _trace_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
