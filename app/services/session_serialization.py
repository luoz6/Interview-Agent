from typing import Any

from app.graphs.interview_state import InterviewMessage, InterviewState
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
    return {
        "session_id": record.session_id,
        "question_id": record.question_id,
        "answer_state": record.answer_state,
        "status": record.status,
        "feedback_json": record.feedback.model_dump(mode="json")
        if record.feedback is not None
        else None,
        "error": record.error,
        "created_at": record.created_at,
    }


def question_evaluation_record_from_row(row: dict) -> QuestionEvaluationRecord:
    return QuestionEvaluationRecord(
        session_id=row["session_id"],
        question_id=row["question_id"],
        answer_state=row["answer_state"],
        status=row["status"],
        feedback=InterviewFeedback.model_validate(row["feedback_json"])
        if row["feedback_json"] is not None
        else None,
        error=row["error"],
        created_at=row["created_at"],
    )
