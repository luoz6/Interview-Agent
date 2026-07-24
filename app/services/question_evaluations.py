from typing import Literal
import hashlib
import json

from pydantic import BaseModel, Field, model_validator

from app.services.report import InterviewFeedback, utc_now_iso


class QuestionEvaluationInputConflict(ValueError):
    pass


class QuestionEvaluationRecord(BaseModel):
    session_id: str
    question_id: str
    answer_state: Literal["answered", "skipped", "unanswered"] = "answered"
    status: Literal["completed", "failed"]
    feedback: InterviewFeedback | None = None
    error: str | None = None
    retrieval_path: str | None = None
    degraded_reason: str | None = None
    evidence_content_sha256: dict[str, str] = Field(default_factory=dict)
    review_input_sha256: str | None = None
    question_input_sha256: str | None = None
    review_engine: str | None = None
    review_graph_schema_version: str | None = None
    output_sha256: str | None = None
    completed_at: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def validate_state(self) -> "QuestionEvaluationRecord":
        if self.status == "completed" and self.feedback is None:
            raise ValueError("completed question evaluations require feedback")
        if self.status == "failed" and not self.error:
            raise ValueError("failed question evaluations require error")
        return self


def question_evaluation_from_feedback(
    *,
    session_id: str,
    feedback: InterviewFeedback,
    answer_state: Literal["answered", "skipped", "unanswered"] | None = None,
    retrieval_path: str | None = None,
    degraded_reason: str | None = None,
    evidence_content_sha256: dict[str, str] | None = None,
    review_input_sha256: str | None = None,
    question_input_sha256: str | None = None,
    review_engine: str | None = None,
    review_graph_schema_version: str | None = None,
) -> QuestionEvaluationRecord:
    output_payload = feedback.model_dump(mode="json")
    return QuestionEvaluationRecord(
        session_id=session_id,
        question_id=feedback.question_id,
        answer_state=answer_state or feedback.answer_state,
        status="completed",
        feedback=feedback,
        retrieval_path=retrieval_path,
        degraded_reason=degraded_reason,
        evidence_content_sha256=dict(evidence_content_sha256 or {}),
        review_input_sha256=review_input_sha256,
        question_input_sha256=question_input_sha256,
        review_engine=review_engine,
        review_graph_schema_version=review_graph_schema_version,
        output_sha256=hashlib.sha256(
            json.dumps(
                output_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        completed_at=utc_now_iso(),
    )
