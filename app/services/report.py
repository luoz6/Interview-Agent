from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


REPORT_SCHEMA_VERSION_V1 = "report-schema-v1"
REPORT_SCHEMA_VERSION_V2 = "report-schema-v2"
REPORT_PRESENTATION_VERSION_V1 = "report-presentation-v1"
REPORT_PRESENTATION_VERSION_V2 = "report-presentation-v2"
REPORT_DIMENSIONS = (
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
)


class ReportGenerationFailed(RuntimeError):
    """Raised when report generation should be marked as failed."""


class ReportGenerationTimeout(ReportGenerationFailed):
    """Raised when report generation times out."""


class ReportQualityFailed(ReportGenerationFailed):
    """Raised when a generated report violates blocking runtime quality rules."""


class ReportOutputFormatError(ValueError):
    """Raised when a provider response cannot be validated as InterviewReport."""


class DimensionScores(BaseModel):
    breadth: int | None = Field(default=None, ge=0, le=100)
    depth: int | None = Field(default=None, ge=0, le=100)
    architecture: int | None = Field(default=None, ge=0, le=100)
    engineering: int | None = Field(default=None, ge=0, le=100)
    communication: int | None = Field(default=None, ge=0, le=100)


class ScoreEvaluation(BaseModel):
    status: Literal["evaluated", "not_evaluated", "insufficient_evidence"]
    reason_code: str
    score: int | None = Field(default=None, ge=0, le=100)
    evidence_count: int = Field(default=0, ge=0)
    eligible_count: int = Field(default=0, ge=0)
    evaluated_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> "ScoreEvaluation":
        if self.status == "evaluated" and self.score is None:
            raise ValueError("evaluated entries require a numeric score")
        if self.status != "evaluated" and self.score is not None:
            raise ValueError("non-evaluated entries cannot contain a numeric score")
        if self.evaluated_count > self.eligible_count:
            raise ValueError("evaluated_count cannot exceed eligible_count")
        return self


class ReportCoverageV2(BaseModel):
    status: Literal["complete", "partial", "none"]
    evaluated_count: int = Field(ge=0)
    total_eligible_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    per_dimension: dict[str, ScoreEvaluation]

    @model_validator(mode="after")
    def validate_coverage(self) -> "ReportCoverageV2":
        if self.evaluated_count > self.total_eligible_count:
            raise ValueError("evaluated_count cannot exceed total_eligible_count")
        if set(self.per_dimension) != set(REPORT_DIMENSIONS):
            raise ValueError("coverage requires all five report dimensions")
        return self


class ReportEvidenceRefV2(BaseModel):
    evidence_ref_id: str = Field(min_length=1, max_length=240)
    namespace: Literal["candidate", "reference"]
    question_id: str | None = Field(default=None, min_length=1)
    source_id: str | None = Field(default=None, min_length=1)
    excerpt: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_namespace(self) -> "ReportEvidenceRefV2":
        if self.namespace == "candidate" and self.question_id is None:
            raise ValueError("candidate evidence requires question_id")
        if self.namespace == "reference" and self.source_id is None:
            raise ValueError("reference evidence requires source_id")
        return self


class ReportClaimV2(BaseModel):
    claim_id: str = Field(min_length=1, max_length=160)
    kind: Literal["conclusion", "strength", "gap", "risk"] = "conclusion"
    text: str = Field(min_length=1, max_length=2000)
    observation_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)


class ReportPriorityActionV2(BaseModel):
    action_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    why_it_matters: str = Field(min_length=1, max_length=2000)
    practice: str = Field(min_length=1, max_length=2000)
    completion_criteria: str = Field(min_length=1, max_length=2000)
    limitation: str | None = Field(default=None, max_length=2000)
    question_refs: list[str] = Field(default_factory=list)
    observation_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)


class ReportLimitationV2(BaseModel):
    limitation_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    reason_code: str = Field(min_length=1, max_length=160)
    observation_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class ReportMissingTechnicalPointV2(BaseModel):
    point_id: str = Field(min_length=1, max_length=160)
    topic: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    observation_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class ReportObservationV2(BaseModel):
    observation_id: str = Field(pattern=r"^obs-[0-9a-f]{16}$")
    type: Literal["strength", "gap", "risk", "limitation"]
    dimension: Literal[
        "breadth",
        "depth",
        "architecture",
        "engineering",
        "communication",
    ]
    normalized_topic: str = Field(min_length=1, max_length=160)
    severity: Literal["low", "medium", "high", "critical"]
    frequency: int = Field(ge=1)
    role_relevance: Literal["low", "medium", "high"]
    evidence_strength: Literal["low", "medium", "high"]
    question_refs: list[str] = Field(default_factory=list)
    answer_evidence_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    confidence_band: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def validate_observation_evidence(self) -> "ReportObservationV2":
        if not (
            self.question_refs
            or self.answer_evidence_refs
            or self.knowledge_refs
        ):
            raise ValueError("observation requires evidence or question refs")
        if self.type != "limitation" and not self.answer_evidence_refs:
            raise ValueError(
                "strength, gap, and risk observations require answer evidence"
            )
        return self


class ReportTechnicalAppendixV2(BaseModel):
    reason_codes: list[str] = Field(default_factory=list)
    report_path: str | None = None
    observations: list[ReportObservationV2] = Field(default_factory=list)
    summary_prompt_version: str | None = None
    summary_prompt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    summary_generation_mode: Literal[
        "deterministic",
        "provider",
        "deterministic_fallback",
    ] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeedbackReference(BaseModel):
    chunk_id: str
    title: str
    source_type: str
    excerpt: str


class InterviewFeedback(BaseModel):
    question_id: str = Field(description="Question identifier")
    question_text: str = Field(description="Original interview question text")
    user_answer: str = Field(description="Summary of the candidate answer")
    answer_state: Literal["answered", "skipped", "unanswered"] = "answered"
    score: int | None = Field(default=None, ge=0, le=100, description="Question score from 0 to 100")
    dimension_scores: DimensionScores
    evaluation_status: Literal["evaluated", "not_evaluated", "insufficient_evidence"] = "evaluated"
    evaluation_reason_code: str = "sufficient_evidence"
    evidence_count: int = Field(default=0, ge=0)
    dimension_evaluations: dict[str, ScoreEvaluation] = Field(default_factory=dict)
    applicable_dimensions: list[str] = Field(default_factory=list)
    dimension_evidence: list[dict[str, Any]] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    rationale: str = Field(description="Why the score was assigned")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")
    answer_structure_suggestion: str | None = Field(
        default=None,
        max_length=2000,
    )
    missing_technical_points: list[ReportMissingTechnicalPointV2] = Field(
        default_factory=list,
    )
    example_rewrite: str | None = Field(default=None, max_length=4000)
    example_rewrite_evidence_refs: list[str] = Field(default_factory=list)
    references: list[FeedbackReference]

    @model_validator(mode="after")
    def validate_evaluation_state(self) -> "InterviewFeedback":
        values = self.dimension_scores.model_dump().values()
        if self.evaluation_status == "evaluated" and self.score is None:
            raise ValueError("evaluated feedback requires a score")
        if self.evaluation_status != "evaluated":
            if self.score is not None or any(value is not None for value in values):
                raise ValueError("non-evaluated feedback cannot contain numeric scores")
        if self.example_rewrite and not self.example_rewrite_evidence_refs:
            raise ValueError("example rewrite requires candidate evidence refs")
        if not self.example_rewrite and self.example_rewrite_evidence_refs:
            raise ValueError("example rewrite refs require example rewrite text")
        return self


class InterviewReport(BaseModel):
    session_id: str
    report_schema_version: Literal["report-schema-v1", "report-schema-v2"] = (
        REPORT_SCHEMA_VERSION_V1
    )
    presentation_version: str = Field(
        default=REPORT_PRESENTATION_VERSION_V1,
        min_length=1,
    )
    overall_score: int | None = Field(default=None, ge=0, le=100)
    overall_dimension_scores: DimensionScores
    generation_status: Literal["complete", "degraded"] = "complete"
    generation_reason_code: str = "normal"
    score_status: Literal["scored", "partial", "unscored"] = "scored"
    score_reason_code: str = "sufficient_evidence"
    coverage_status: Literal["complete", "partial", "none"] = "complete"
    evaluated_count: int | None = Field(default=None, ge=0)
    total_eligible_count: int | None = Field(default=None, ge=0)
    evidence_count: int | None = Field(default=None, ge=0)
    dimension_evaluations: dict[str, ScoreEvaluation] = Field(default_factory=dict)
    question_evaluations: dict[str, ScoreEvaluation] = Field(default_factory=dict)
    coverage: ReportCoverageV2 | None = None
    summary_observations: list[ReportClaimV2] = Field(default_factory=list)
    strengths: list[ReportClaimV2] = Field(default_factory=list)
    priority_actions: list[ReportPriorityActionV2] = Field(
        default_factory=list,
        max_length=3,
    )
    limitations: list[ReportLimitationV2] = Field(default_factory=list)
    evidence_refs: list[ReportEvidenceRefV2] = Field(default_factory=list)
    technical_appendix: ReportTechnicalAppendixV2 = Field(
        default_factory=ReportTechnicalAppendixV2
    )
    report_path: Literal["microbatch", "full_session", "heuristic", "legacy"] = "full_session"
    scoring_rubric_version: str = "interview-quality-rubric-v3.3-candidate"
    scoring_rubric_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
    is_fallback: bool = False

    @model_validator(mode="after")
    def validate_score_state(self) -> "InterviewReport":
        dimension_values = self.overall_dimension_scores.model_dump().values()
        if self.score_status == "unscored":
            if self.overall_score is not None or any(
                value is not None for value in dimension_values
            ):
                raise ValueError("unscored reports cannot contain numeric scores")
        elif self.overall_score is None:
            raise ValueError("scored or partial reports require overall_score")
        if self.score_status == "partial":
            if self.evaluated_count is None or self.total_eligible_count is None:
                raise ValueError("partial reports require evaluated and eligible counts")
            if self.evaluated_count > self.total_eligible_count:
                raise ValueError("evaluated_count cannot exceed total_eligible_count")
        if self.score_status == "scored" and self.overall_score is None:
            raise ValueError("scored reports require overall_score")
        if self.report_schema_version == REPORT_SCHEMA_VERSION_V2:
            if self.coverage is None:
                raise ValueError("report schema v2 requires structured coverage")
            if self.coverage.status != self.coverage_status:
                raise ValueError("structured coverage status does not match report")
            if self.coverage.evaluated_count != (self.evaluated_count or 0):
                raise ValueError("structured coverage evaluated_count does not match report")
            if self.coverage.total_eligible_count != (
                self.total_eligible_count or 0
            ):
                raise ValueError("structured coverage denominator does not match report")
            if self.coverage.evidence_count != (self.evidence_count or 0):
                raise ValueError("structured coverage evidence_count does not match report")
            if self.coverage.per_dimension != self.dimension_evaluations:
                raise ValueError("structured dimension coverage does not match report")
            evidence_ids = [item.evidence_ref_id for item in self.evidence_refs]
            if len(evidence_ids) != len(set(evidence_ids)):
                raise ValueError("report evidence_ref_id values must be unique")
            available = set(evidence_ids)
            observation_ids = {
                item.observation_id
                for item in self.technical_appendix.observations
            }
            question_ids = {item.question_id for item in self.feedbacks}
            claims = [*self.summary_observations, *self.strengths]
            for claim in claims:
                if not set(claim.evidence_refs).issubset(available):
                    raise ValueError("report claim contains an unknown evidence ref")
                if claim.observation_refs and not set(
                    claim.observation_refs
                ).issubset(observation_ids):
                    raise ValueError("report claim contains an unknown observation ref")
            for action in self.priority_actions:
                if not set(action.evidence_refs).issubset(available):
                    raise ValueError("priority action contains an unknown evidence ref")
                if action.observation_refs and not set(
                    action.observation_refs
                ).issubset(observation_ids):
                    raise ValueError(
                        "priority action contains an unknown observation ref"
                    )
                if action.question_refs and not set(
                    action.question_refs
                ).issubset(question_ids):
                    raise ValueError("priority action contains an unknown question ref")
            for limitation in self.limitations:
                if not set(limitation.evidence_refs).issubset(available):
                    raise ValueError("report limitation contains an unknown evidence ref")
                if limitation.observation_refs and not set(
                    limitation.observation_refs
                ).issubset(observation_ids):
                    raise ValueError(
                        "report limitation contains an unknown observation ref"
                    )
            for feedback in self.feedbacks:
                for point in feedback.missing_technical_points:
                    if not set(point.evidence_refs).issubset(available):
                        raise ValueError(
                            "technical point contains an unknown evidence ref"
                        )
                    if not set(point.observation_refs).issubset(
                        observation_ids
                    ):
                        raise ValueError(
                            "technical point contains an unknown observation ref"
                        )
                if not set(
                    feedback.example_rewrite_evidence_refs
                ).issubset(available):
                    raise ValueError(
                        "example rewrite contains an unknown evidence ref"
                    )
                if any(
                    ref != f"candidate:{feedback.question_id}:answer"
                    for ref in feedback.example_rewrite_evidence_refs
                ):
                    raise ValueError(
                        "example rewrite requires same-question candidate evidence"
                    )
        return self


class ReportProgress(BaseModel):
    stage: Literal["retrieving", "analyzing", "aggregating", "completed"]
    percent: int = Field(ge=0, le=100)
    message: str
    current_question_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def utc_now_iso() -> str:
    # Kept local to report models on purpose; importing graph-state helpers here
    # would invert the service dependency direction.
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ReportRecord(BaseModel):
    status: Literal["processing", "completed", "failed"]
    progress: ReportProgress | None = None
    report: InterviewReport | None = None
    error: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    finished_at: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ReportRecord":
        if self.status == "processing":
            if self.progress is None or self.report is not None or self.error is not None:
                raise ValueError(
                    "processing report records require progress and cannot contain report or error"
                )
        if self.status == "completed" and self.report is None:
            raise ValueError("completed report records require report")
        if self.status == "failed" and not self.error:
            raise ValueError("failed report records require error")
        return self
