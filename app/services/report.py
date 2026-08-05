from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    rationale: str = Field(description="Why the score was assigned")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")
    references: list[FeedbackReference]

    @model_validator(mode="after")
    def validate_evaluation_state(self) -> "InterviewFeedback":
        values = self.dimension_scores.model_dump().values()
        if self.evaluation_status == "evaluated" and self.score is None:
            raise ValueError("evaluated feedback requires a score")
        if self.evaluation_status != "evaluated":
            if self.score is not None or any(value is not None for value in values):
                raise ValueError("non-evaluated feedback cannot contain numeric scores")
        return self


class InterviewReport(BaseModel):
    session_id: str
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
