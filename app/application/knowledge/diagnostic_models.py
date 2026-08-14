from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.knowledge.evidence import EvidenceDecision
from app.domain.knowledge.retrieval import RetrievalIntent


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagCapabilitySummary(SafeModel):
    diagnostic_ui: bool
    live_inspector: bool
    eval_artifacts: bool
    authored_eval_queries: bool
    corpus_write: bool = False
    access_mode: Literal["loopback"] = "loopback"


class PromotionBlocker(SafeModel):
    code: str
    severity: Literal["hard_stop", "warning"]
    blocks: tuple[str, ...]
    observed_evidence: str
    required_action: str
    last_evaluated_at: datetime


class PromotionDecision(SafeModel):
    allowed: bool = False
    decision_version: str = "knowledge-promotion-decision-v1"
    blockers: tuple[PromotionBlocker, ...]


class RagOverviewResponse(SafeModel):
    schema_version: str = "rag-overview-v1"
    generated_at: datetime
    formal_engine: str
    candidate_engine: str
    hybrid_rollout_percent: int = Field(ge=0, le=100)
    shadow_enabled: bool
    remote_reranker_enabled: bool
    evidence_gate_enabled: bool
    corpus: dict[str, str | int | bool]
    embedding: dict[str, str | int | bool]
    profiles: tuple[dict[str, str | int | float | bool], ...]
    component_versions: dict[str, str]
    capabilities: RagCapabilitySummary
    promotion: PromotionDecision
    release_evidence: dict[str, str | int | bool]


class RetrievalInspectionRequest(SafeModel):
    mode: Literal["live", "current_engine_rerun"] = "live"
    query_text: str = Field(min_length=1, max_length=4000)
    intent: RetrievalIntent = RetrievalIntent.EVAL
    profile_id: str = Field(default="question-review", min_length=1, max_length=100)
    engine: Literal["legacy", "hybrid-v2"] = "hybrid-v2"
    domains: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    canonical_tags: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()

    @field_validator("query_text")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query_text must not be blank")
        return value


class RetrievalCompareRequest(SafeModel):
    """One privacy-safe request that compares both supported engines."""

    query_text: str = Field(min_length=1, max_length=4000)
    intent: RetrievalIntent = RetrievalIntent.EVAL
    profile_id: str = Field(default="question-review", min_length=1, max_length=100)
    domains: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    canonical_tags: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()

    @field_validator("query_text")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query_text must not be blank")
        return value


class SafeRankingExplanation(SafeModel):
    base_score_source: str
    base_score: float
    exact_term_boost: float
    routing_tag_boost: float
    eligibility_score: float
    eligible: bool
    final_rerank_score: float | None
    tie_break_fusion_rank: int | None
    reason_codes: tuple[str, ...]


class SafeRetrievalCandidate(SafeModel):
    candidate_id: str
    title: str
    safe_excerpt: str
    domain: str
    topic: str
    tags: tuple[str, ...]
    source_type: str
    authority_status: str
    content_sha256: str
    corpus_manifest_sha256: str
    semantic_rank: int | None
    semantic_score: float | None
    lexical_rank: int | None
    lexical_score: float | None
    fusion_rank: int | None
    fusion_score: float | None
    rerank_rank: int | None
    rerank_score: float | None
    channel_hits: tuple[str, ...]
    matched_terms: tuple[str, ...]
    ranking_explanation: SafeRankingExplanation | None
    selected: bool


class ConsumerActionRecord(SafeModel):
    recording_status: Literal["recorded", "not_recorded"] = "not_recorded"
    consumer: str | None = None
    action: str | None = None
    policy_version: str | None = None
    reason_codes: tuple[str, ...] = ()
    public_message: str = "Not recorded / no unified policy"


class SafeInspectionInputs(SafeModel):
    intent: str = "not_recorded"
    requested_domains: tuple[str, ...] = ()
    requested_topics: tuple[str, ...] = ()
    canonical_tags: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()


class SafeRetrievalInspectionResponse(SafeModel):
    schema_version: str = "rag-retrieval-inspection-v1"
    request_id: str
    mode: Literal["live", "artifact_replay", "current_engine_rerun"]
    created_at: datetime
    diagnostic_fidelity: Literal["live", "full_snapshot", "partial_historical"]
    engine: str
    profile_id: str
    profile_version: str
    trace_schema_version: str
    inspection_inputs: SafeInspectionInputs
    query_facts: dict[str, str | int]
    resolved_profile: dict
    routing_summary: dict
    channel_summary: tuple[dict, ...]
    candidates: tuple[SafeRetrievalCandidate, ...]
    evidence_decision: EvidenceDecision | None
    consumer_action: ConsumerActionRecord
    latency_ms: dict[str, float | None]
    degraded_reasons: tuple[str, ...]
    component_versions: dict[str, str]
    provider_call_possible: bool
    artifact_identity: dict[str, str | int | bool]
    artifact_sha256: str | None = None
    case_id: str | None = None


class SafeCompareSide(SafeModel):
    status: Literal["success", "failed", "timeout"]
    inspection: SafeRetrievalInspectionResponse | None = None
    failure_code: Literal[
        "retrieval_failed",
        "retrieval_timeout",
    ] | None = None


class SafeTopKOverlap(SafeModel):
    k: int = Field(ge=1)
    overlap_count: int = Field(ge=0)
    overlap_ratio: float = Field(ge=0, le=1)
    candidate_ids: tuple[str, ...]


class SafeRankChange(SafeModel):
    candidate_id: str
    legacy_rank: int | None
    hybrid_rank: int | None
    rank_delta: int | None
    legacy_selected: bool
    hybrid_selected: bool


class SafeRetrievalCompareResponse(SafeModel):
    schema_version: str = "rag-retrieval-compare-v1"
    created_at: datetime
    request_id: str
    requested_profile_id: str
    corpus_manifest_sha256: str | None = None
    legacy: SafeCompareSide
    hybrid: SafeCompareSide
    top_k_overlap: SafeTopKOverlap | None = None
    rank_changes: tuple[SafeRankChange, ...] = ()
    selected_evidence_changed: bool | None = None
    evidence_decision_changed: bool | None = None
    latency_delta_ms: float | None = None


class ArtifactSummary(SafeModel):
    artifact_sha256: str
    schema_version: str
    dataset_version: str
    split: str
    engine_version: str
    created_at: datetime
    case_count: int
    annotation_status: str
    human_annotator_count: int = Field(ge=0)
    independent_evidence_eligible: bool
    holdout_status: Literal[
        "not_applicable", "historical_diagnostic", "sealed", "formal"
    ]
    corpus_manifest_sha256: str
    embedding_provider: str
    embedding_model: str
    embedding_revision: str
    embedding_dimension: int = Field(ge=1)
    code_revision: str
    code_tree_sha256: str
    profile_id: str
    profile_version: str
    profile_sha256: str
    promotion_status: Literal["blocked", "not_evaluated"] = "blocked"
    diagnostic_fidelity: Literal["full_snapshot", "partial_historical"]
    metrics: dict[str, object]


class ArtifactCatalogResponse(SafeModel):
    schema_version: str = "rag-artifact-catalog-v1"
    artifacts: tuple[ArtifactSummary, ...]


class ArtifactDetailResponse(SafeModel):
    schema_version: str = "rag-artifact-detail-v1"
    artifact: ArtifactSummary
    paired_comparisons: tuple["PairedEvaluationSummary", ...] = ()


class PairedEvaluationSummary(SafeModel):
    artifact_sha256: str
    dataset_version: str
    split: str
    baseline_artifact_sha256: str
    candidate_artifact_sha256: str
    baseline_engine_version: str
    candidate_engine_version: str
    thresholds_passed: bool | None
    failed_thresholds: tuple[str, ...]
    metrics: tuple[dict[str, str | float], ...]
    case_type_deltas: dict[str, dict[str, float]]


class PairedEvaluationsResponse(SafeModel):
    schema_version: str = "rag-paired-evaluations-v1"
    comparisons: tuple[PairedEvaluationSummary, ...]


class NoEvidenceConfusionSummary(SafeModel):
    correct_evidence: int = Field(ge=0)
    false_abstention: int = Field(ge=0)
    false_evidence: int = Field(ge=0)
    correct_abstention: int = Field(ge=0)
    total_case_count: int = Field(ge=0)
    expected_no_evidence_count: int = Field(ge=0)
    abstention_count: int = Field(ge=0)
    no_evidence_prevalence: float = Field(ge=0, le=1)
    abstention_rate: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


class EvalCaseSummary(SafeModel):
    case_id: str
    case_type: str
    evaluation_group: str
    primary_relevant_chunk_ids: tuple[str, ...]
    accepted_related_chunk_ids: tuple[str, ...]
    excluded_chunk_ids: tuple[str, ...]
    expected_no_evidence: bool
    availability: str
    selected_evidence_ids: tuple[str, ...]
    declared_no_evidence: bool
    latency_ms: float
    reason_codes: tuple[str, ...]
    diagnostic_fidelity: Literal["full_snapshot", "partial_historical"]
    diagnostic_snapshot_ref: str | None = None


class EvalCasesResponse(SafeModel):
    schema_version: str = "rag-eval-cases-v1"
    artifact_sha256: str
    cases: tuple[EvalCaseSummary, ...]


class CorpusUnitSummary(SafeModel):
    unit_id: str
    title: str
    domain: str
    topic: str
    source_type: str
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    source_authority: str
    review_status: str
    version: str
    retirement_status: Literal["active", "retired", "not_recorded"]
    embedding_status: Literal["active", "unavailable", "not_recorded"]
    content_sha256: str


class CorpusResponse(SafeModel):
    schema_version: str = "rag-corpus-v1"
    corpus_version: str
    manifest_sha256: str
    chunk_count: int
    embedding: dict[str, str | int | bool]
    activation_status: Literal["active", "unavailable", "not_recorded"]
    retired_versions: tuple[str, ...]
    write_enabled: bool = False
    units: tuple[CorpusUnitSummary, ...]


CorpusDomain = Literal[
    "python",
    "fastapi",
    "redis",
    "mysql",
    "postgresql",
    "kafka",
    "rocketmq",
    "system-design",
    "reliability",
]
CorpusSourceType = Literal["theory", "engineering_guide", "expert_benchmark"]
CorpusContentKind = Literal[
    "mechanism",
    "failure_mode",
    "engineering_practice",
    "benchmark",
    "hard_negative",
]
CorpusDifficulty = Literal["beginner", "intermediate", "advanced"]


class CorpusReferenceInput(SafeModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    source_kind: Literal["official_cn", "secondary_cn"]
    publisher: str = Field(min_length=1, max_length=200)


class CorpusEntryInput(SafeModel):
    unit_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    title: str = Field(min_length=1, max_length=300)
    domain: CorpusDomain
    topic: str = Field(min_length=1, max_length=128)
    source_type: CorpusSourceType
    content_kind: CorpusContentKind
    difficulty: CorpusDifficulty
    tags: tuple[str, ...] = Field(min_length=2, max_length=16)
    aliases: tuple[str, ...] = Field(min_length=1, max_length=8)
    technical_terms: tuple[str, ...] = Field(default=(), max_length=12)
    question_patterns: tuple[str, ...] = Field(min_length=2, max_length=5)
    references: tuple[CorpusReferenceInput, ...] = Field(min_length=1, max_length=8)
    content: str = Field(min_length=1, max_length=50_000)


class CorpusValidationIssue(SafeModel):
    field: str
    code: str
    message: str


class CorpusValidateRequest(SafeModel):
    entry: CorpusEntryInput


class CorpusValidateResponse(SafeModel):
    schema_version: str = "rag-corpus-validation-v1"
    valid: bool
    validation_sha256: str
    current_corpus_version: str
    current_manifest_sha256: str
    content_sha256: str
    chinese_character_count: int = Field(ge=0)
    provider_call_required: bool
    estimated_embedding_count: int = Field(ge=0)
    issues: tuple[CorpusValidationIssue, ...]


class CorpusReleaseRequest(SafeModel):
    entry: CorpusEntryInput
    corpus_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    expected_active_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_provider_cost: bool
    confirm_activation: bool


class CorpusReleaseResponse(SafeModel):
    schema_version: str = "rag-corpus-release-v1"
    corpus_version: str
    manifest_sha256: str
    discovered: int = Field(ge=1)
    reused: int = Field(ge=0)
    embedded: int = Field(ge=0)
    activated: int = Field(ge=1)
    provider_name: str
    model_name: str
    model_revision: str
    dimension: int = Field(ge=1)
    replayed: bool = False


class EvidenceTraceStage(SafeModel):
    stage: Literal[
        "base_evidence_bundle",
        "question_evidence_binding",
        "review_evidence_binding",
        "reviewer_decision",
        "followup_decision",
    ]
    recording_status: Literal["recorded", "not_recorded"]
    record_id: str | None = None
    parent_record_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    evidence_refs: tuple["SafeEvidenceTraceRef", ...] = ()
    corpus_manifest_sha256: str = ""
    decision: EvidenceDecision | None = None
    created_at: datetime | None = None
    note: str = ""


class SafeEvidenceTraceRef(SafeModel):
    evidence_id: str
    title: str
    domain: str
    topic: str
    source_type: str
    content_sha256: str
    corpus_manifest_sha256: str


class EvidenceTraceResponse(SafeModel):
    schema_version: str = "rag-evidence-trace-v1"
    trace_id: str
    generated_at: datetime
    stages: tuple[EvidenceTraceStage, ...]
    safe_boundary: tuple[str, ...] = (
        "raw_query_excluded",
        "answer_excluded",
        "resume_and_jd_excluded",
        "provider_payload_excluded",
        "chain_of_thought_excluded",
    )
