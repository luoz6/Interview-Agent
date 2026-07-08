from datetime import datetime, timezone
from typing import Literal

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
    breadth: int = Field(ge=0, le=100)
    depth: int = Field(ge=0, le=100)
    architecture: int = Field(ge=0, le=100)
    engineering: int = Field(ge=0, le=100)
    communication: int = Field(ge=0, le=100)


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
    score: int = Field(ge=0, le=100, description="Question score from 0 to 100")
    dimension_scores: DimensionScores
    rationale: str = Field(description="Why the score was assigned")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")
    references: list[FeedbackReference]


class InterviewReport(BaseModel):
    session_id: str
    overall_score: int = Field(ge=0, le=100)
    overall_dimension_scores: DimensionScores
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
    is_fallback: bool = False


class ReportProgress(BaseModel):
    stage: Literal["retrieving", "analyzing", "aggregating", "completed"]
    percent: int = Field(ge=0, le=100)
    message: str
    current_question_id: str | None = None


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
