from __future__ import annotations

from typing import Any

from app.adapters.postgres.row_mappers.errors import require_supported_row_version
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewFeedback


QUESTION_EVALUATION_ROW_SCHEMA_VERSION = "question-evaluation-row-v1"


class QuestionEvaluationRowMapper:
    CURRENT_VERSION = QUESTION_EVALUATION_ROW_SCHEMA_VERSION
    BACKFILL_POLICY = "missing-column-and-legacy-feedback-envelope-mean-v1"

    @classmethod
    def to_row(cls, record: QuestionEvaluationRecord) -> dict[str, Any]:
        feedback_json = None
        if record.feedback is not None:
            feedback_json = {
                "feedback": record.feedback.model_dump(mode="json"),
                "record_metadata": {
                    "retrieval_path": record.retrieval_path,
                    "degraded_reason": record.degraded_reason,
                    "evidence_content_sha256": record.evidence_content_sha256,
                    "review_input_sha256": record.review_input_sha256,
                    "question_input_sha256": record.question_input_sha256,
                    "review_engine": record.review_engine,
                    "review_graph_schema_version": record.review_graph_schema_version,
                    "output_sha256": record.output_sha256,
                    "completed_at": record.completed_at,
                },
            }
        return {
            "session_id": record.session_id,
            "question_id": record.question_id,
            "answer_state": record.answer_state,
            "status": record.status,
            "feedback_json": feedback_json,
            "error": record.error,
            "created_at": record.created_at,
            "review_input_sha256": record.review_input_sha256,
            "question_input_sha256": record.question_input_sha256,
            "review_engine": record.review_engine,
            "review_graph_schema_version": record.review_graph_schema_version,
            "output_sha256": record.output_sha256,
            "completed_at": record.completed_at,
            "row_schema_version": cls.CURRENT_VERSION,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> QuestionEvaluationRecord:
        require_supported_row_version(
            row.get("row_schema_version"),
            row_type="question evaluation",
            current_version=cls.CURRENT_VERSION,
        )
        feedback_payload = row["feedback_json"]
        if isinstance(feedback_payload, dict) and "feedback" in feedback_payload:
            metadata = feedback_payload.get("record_metadata") or {}
            feedback_payload = feedback_payload["feedback"]
        else:
            metadata = {}
        return QuestionEvaluationRecord(
            session_id=row["session_id"],
            question_id=row["question_id"],
            answer_state=row["answer_state"],
            status=row["status"],
            feedback=InterviewFeedback.model_validate(feedback_payload)
            if feedback_payload is not None
            else None,
            retrieval_path=metadata.get("retrieval_path"),
            degraded_reason=metadata.get("degraded_reason"),
            evidence_content_sha256=metadata.get("evidence_content_sha256") or {},
            review_input_sha256=row.get("review_input_sha256")
            or metadata.get("review_input_sha256"),
            question_input_sha256=row.get("question_input_sha256")
            or metadata.get("question_input_sha256"),
            review_engine=row.get("review_engine") or metadata.get("review_engine"),
            review_graph_schema_version=row.get("review_graph_schema_version")
            or metadata.get("review_graph_schema_version"),
            output_sha256=row.get("output_sha256") or metadata.get("output_sha256"),
            completed_at=row.get("completed_at") or metadata.get("completed_at"),
            error=row["error"],
            created_at=row["created_at"],
        )
