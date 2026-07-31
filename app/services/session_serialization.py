from typing import Any

from app.graphs.interview_state import (
    InterviewMessage,
    InterviewState,
    SUPPORTED_MEMORY_POLICY_VERSIONS,
    default_memory_policy_for_engine,
)
from app.services.prep import InterviewPlan
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import (
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportRecord,
)


def session_row_from_state(state: InterviewState) -> dict[str, Any]:
    return {
        "session_id": state["session_id"],
        "plan_json": state["plan"].model_dump(mode="json"),
        "current_index": state["current_index"],
        "status": state["status"],
        "phase": state["phase"],
        "phase_status": state["phase_status"],
        "review_status": state["review_status"],
        "job_description": state["job_description"],
        "resume_text": state["resume_text"],
        "job_tags": list(state["job_tags"]),
        "decision_json": state["decision"],
        "pending_output": state["pending_output"],
        "skipped_question_ids": list(state.get("skipped_question_ids", [])),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "state_version": state["state_version"],
        "checkpoint_version": state["checkpoint_version"],
        "last_checkpoint_at": state.get("last_checkpoint_at"),
        "last_command_id": state.get("last_command_id"),
        "workflow_engine": state.get("workflow_engine", "legacy"),
        "graph_schema_version": state.get("graph_schema_version"),
        "projection_sha256": state.get("projection_sha256"),
        "memory_policy_version": state["memory_policy_version"],
        "deletion_status": state.get("deletion_status", "active"),
    }


def message_to_row(
    session_id: str,
    sequence_no: int,
    message: InterviewMessage,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "sequence_no": sequence_no,
        "role": message["role"],
        "content": message["content"],
        "question_id": message["question_id"],
    }


def state_from_rows(
    session_row: dict[str, Any],
    message_rows: list[dict[str, Any]],
) -> InterviewState:
    workflow_engine = session_row.get("workflow_engine", "legacy")
    memory_policy_version = session_row.get("memory_policy_version")
    if memory_policy_version is None:
        memory_policy_version = default_memory_policy_for_engine(
            workflow_engine
        )
    if memory_policy_version not in SUPPORTED_MEMORY_POLICY_VERSIONS:
        raise ValueError("unsupported stored interview memory policy version")
    return {
        "session_id": session_row["session_id"],
        "plan": InterviewPlan.model_validate(session_row["plan_json"]),
        "current_index": int(session_row["current_index"]),
        "messages": [
            {
                "role": row["role"],
                "content": row["content"],
                "question_id": row["question_id"],
            }
            for row in sorted(message_rows, key=lambda row: int(row["sequence_no"]))
        ],
        "decision": session_row.get("decision_json"),
        "pending_output": session_row.get("pending_output"),
        "status": session_row["status"],
        "phase": session_row.get("phase", "interview"),
        "phase_status": session_row.get("phase_status", "active"),
        "review_status": session_row.get("review_status", "idle"),
        "job_description": session_row["job_description"],
        "resume_text": session_row["resume_text"],
        "job_tags": list(session_row["job_tags"]),
        "skipped_question_ids": list(session_row.get("skipped_question_ids") or []),
        "started_at": session_row.get("started_at") or "",
        "finished_at": session_row.get("finished_at"),
        "state_version": int(session_row.get("state_version", 1)),
        "checkpoint_version": int(session_row.get("checkpoint_version", 1)),
        "last_checkpoint_at": session_row.get("last_checkpoint_at"),
        "last_command_id": session_row.get("last_command_id"),
        "workflow_engine": workflow_engine,
        "graph_schema_version": session_row.get("graph_schema_version"),
        "projection_sha256": session_row.get("projection_sha256"),
        "memory_policy_version": memory_policy_version,
        "deletion_status": session_row.get("deletion_status", "active"),
    }


def report_record_to_row(record: ReportRecord) -> dict[str, Any]:
    return {
        "status": record.status,
        "progress_json": record.progress.model_dump(mode="json")
        if record.progress is not None
        else None,
        "report_json": record.report.model_dump(mode="json")
        if record.report is not None
        else None,
        "error": record.error,
        "created_at": record.created_at,
        "finished_at": record.finished_at,
    }


def report_record_from_row(row: dict[str, Any]) -> ReportRecord:
    progress = (
        ReportProgress.model_validate(row["progress_json"])
        if row.get("progress_json") is not None
        else None
    )
    report = (
        InterviewReport.model_validate(row["report_json"])
        if row.get("report_json") is not None
        else None
    )
    if row.get("created_at"):
        return ReportRecord(
            status=row["status"],
            progress=progress,
            report=report,
            error=row.get("error"),
            created_at=row["created_at"],
            finished_at=row.get("finished_at"),
        )
    return ReportRecord(
        status=row["status"],
        progress=progress,
        report=report,
        error=row.get("error"),
        finished_at=row.get("finished_at"),
    )


def question_evaluation_record_to_row(record: QuestionEvaluationRecord) -> dict:
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
    }


def question_evaluation_record_from_row(row: dict) -> QuestionEvaluationRecord:
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
