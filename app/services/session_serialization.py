from typing import Any

from app.graphs.interview_state import InterviewMessage, InterviewState
from app.services.prep import InterviewPlan
from app.services.report import InterviewReport, ReportProgress, ReportRecord


def session_row_from_state(state: InterviewState) -> dict[str, Any]:
    return {
        "session_id": state["session_id"],
        "plan_json": state["plan"].model_dump(mode="json"),
        "current_index": state["current_index"],
        "status": state["status"],
        "job_description": state["job_description"],
        "resume_text": state["resume_text"],
        "job_tags": list(state["job_tags"]),
        "decision_json": state["decision"],
        "pending_output": state["pending_output"],
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
        "job_description": session_row["job_description"],
        "resume_text": session_row["resume_text"],
        "job_tags": list(session_row["job_tags"]),
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
    return ReportRecord(
        status=row["status"],
        progress=progress,
        report=report,
        error=row.get("error"),
    )
