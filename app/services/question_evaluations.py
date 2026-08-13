from typing import Literal
import hashlib
import json

from pydantic import BaseModel, Field, model_validator

from app.domain.knowledge.evidence import ReviewEvidenceBinding
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
    answer_quality_score: int | None = Field(default=None, ge=0, le=100)
    evaluation_confidence: Literal["high", "medium", "low", "not_scorable"] | None = None
    evidence_availability: Literal["available", "degraded", "unavailable"] | None = None
    evidence_sufficiency: Literal[
        "sufficient", "weak", "insufficient", "empty", "not_evaluated"
    ] | None = None
    evidence_consistency: Literal[
        "consistent", "possible_conflict", "confirmed_conflict", "not_evaluated"
    ] | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    gate_reason_codes: list[str] = Field(default_factory=list)
    evidence_binding_id: str | None = None
    review_evidence_binding: ReviewEvidenceBinding | None = None
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
        if (
            self.evidence_binding_id
            and self.review_evidence_binding
            and self.evidence_binding_id != self.review_evidence_binding.binding_id
        ):
            raise ValueError(
                "evidence_binding_id must match review_evidence_binding.binding_id"
            )
        if self.review_evidence_binding is not None:
            decision = self.review_evidence_binding.decision
            expected_values = {
                "evaluation_confidence": decision.evaluation_confidence.value,
                "evidence_availability": decision.availability.value,
                "evidence_sufficiency": decision.sufficiency.value,
                "evidence_consistency": decision.consistency.value,
            }
            for field_name, expected in expected_values.items():
                actual = getattr(self, field_name)
                if actual is not None and actual != expected:
                    raise ValueError(
                        f"{field_name} must match review_evidence_binding.decision"
                    )
            if self.evidence_ids and tuple(self.evidence_ids) != (
                self.review_evidence_binding.final_evidence_ids
            ):
                raise ValueError(
                    "evidence_ids must match review_evidence_binding.final_evidence_ids"
                )
            if self.gate_reason_codes and tuple(self.gate_reason_codes) != (
                decision.reason_codes
            ):
                raise ValueError(
                    "gate_reason_codes must match review_evidence_binding.decision"
                )
        return self


def question_evaluation_from_feedback(
    *,
    session_id: str,
    feedback: InterviewFeedback,
    answer_state: Literal["answered", "skipped", "unanswered"] | None = None,
    retrieval_path: str | None = None,
    degraded_reason: str | None = None,
    evidence_content_sha256: dict[str, str] | None = None,
    evaluation_confidence: str | None = None,
    evidence_availability: str | None = None,
    evidence_sufficiency: str | None = None,
    evidence_consistency: str | None = None,
    evidence_ids: list[str] | None = None,
    gate_reason_codes: list[str] | None = None,
    evidence_binding_id: str | None = None,
    review_evidence_binding: ReviewEvidenceBinding | dict | None = None,
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
        answer_quality_score=feedback.score,
        evaluation_confidence=evaluation_confidence,
        evidence_availability=evidence_availability,
        evidence_sufficiency=evidence_sufficiency,
        evidence_consistency=evidence_consistency,
        evidence_ids=list(evidence_ids or []),
        gate_reason_codes=list(gate_reason_codes or []),
        evidence_binding_id=evidence_binding_id,
        review_evidence_binding=review_evidence_binding,
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
