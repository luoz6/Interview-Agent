from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReportGenerationFailed(RuntimeError):
    """Raised when report generation should be marked as failed."""


class ReportGenerationTimeout(ReportGenerationFailed):
    """Raised when report generation times out."""


class InterviewFeedback(BaseModel):
    question_id: str = Field(description="Question identifier")
    question_text: str = Field(description="Original interview question text")
    user_answer: str = Field(description="Summary of the candidate answer")
    score: int = Field(ge=0, le=100, description="Question score from 0 to 100")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")


class InterviewReport(BaseModel):
    session_id: str
    overall_score: int = Field(ge=0, le=100)
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
    is_fallback: bool = False


class ReportRecord(BaseModel):
    status: Literal["processing", "completed", "failed"]
    report: InterviewReport | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ReportRecord":
        if self.status == "processing" and (
            self.report is not None or self.error is not None
        ):
            raise ValueError("processing report records cannot contain report or error")
        if self.status == "completed" and self.report is None:
            raise ValueError("completed report records require report")
        if self.status == "failed" and not self.error:
            raise ValueError("failed report records require error")
        return self
