from __future__ import annotations

from typing import Any

from app.adapters.postgres.row_mappers.errors import (
    require_supported_row_version,
)
from app.graphs.interview_state import (
    InterviewMessage,
    InterviewState,
    SUPPORTED_MEMORY_POLICY_VERSIONS,
    default_memory_policy_for_engine,
)
from app.services.prep import InterviewPlan


SESSION_ROW_SCHEMA_VERSION = "session-row-v1"
MESSAGE_ROW_SCHEMA_VERSION = "message-row-v1"


class MemoryPolicyRowMapper:
    CURRENT_VERSION = "memory-policy-row-v1"
    BACKFILL_POLICY = "derive-from-workflow-engine-v1"

    @classmethod
    def from_stored_value(
        cls,
        *,
        workflow_engine: str,
        memory_policy_version: object,
    ) -> str:
        version = memory_policy_version
        if version is None:
            version = default_memory_policy_for_engine(workflow_engine)
        if version not in SUPPORTED_MEMORY_POLICY_VERSIONS:
            raise ValueError("unsupported stored interview memory policy version")
        return str(version)


class MessageRowMapper:
    CURRENT_VERSION = MESSAGE_ROW_SCHEMA_VERSION
    BACKFILL_POLICY = "missing-column-means-message-row-v1"

    @classmethod
    def to_row(
        cls,
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
            "row_schema_version": cls.CURRENT_VERSION,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> InterviewMessage:
        require_supported_row_version(
            row.get("row_schema_version"),
            row_type="message",
            current_version=cls.CURRENT_VERSION,
        )
        return {
            "role": row["role"],
            "content": row["content"],
            "question_id": row["question_id"],
        }


class SessionRowMapper:
    CURRENT_VERSION = SESSION_ROW_SCHEMA_VERSION
    BACKFILL_POLICY = "missing-column-means-session-row-v1"

    @classmethod
    def to_row(cls, state: InterviewState) -> dict[str, Any]:
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
            "row_schema_version": cls.CURRENT_VERSION,
        }

    @classmethod
    def from_rows(
        cls,
        session_row: dict[str, Any],
        message_rows: list[dict[str, Any]],
    ) -> InterviewState:
        require_supported_row_version(
            session_row.get("row_schema_version"),
            row_type="session",
            current_version=cls.CURRENT_VERSION,
        )
        workflow_engine = session_row.get("workflow_engine", "legacy")
        memory_policy_version = MemoryPolicyRowMapper.from_stored_value(
            workflow_engine=workflow_engine,
            memory_policy_version=session_row.get("memory_policy_version"),
        )
        ordered_messages = sorted(
            message_rows,
            key=lambda row: int(row["sequence_no"]),
        )
        return {
            "session_id": session_row["session_id"],
            "plan": InterviewPlan.model_validate(session_row["plan_json"]),
            "current_index": int(session_row["current_index"]),
            "messages": [MessageRowMapper.from_row(row) for row in ordered_messages],
            "decision": session_row.get("decision_json"),
            "pending_output": session_row.get("pending_output"),
            "status": session_row["status"],
            "phase": session_row.get("phase", "interview"),
            "phase_status": session_row.get("phase_status", "active"),
            "review_status": session_row.get("review_status", "idle"),
            "job_description": session_row["job_description"],
            "resume_text": session_row["resume_text"],
            "job_tags": list(session_row["job_tags"]),
            "skipped_question_ids": list(
                session_row.get("skipped_question_ids") or []
            ),
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
