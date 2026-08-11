from typing import Any

from app.graphs.interview_graph import fallback_followup
from app.graphs.interview_state import InterviewState, get_current_question
from app.graphs.interview_transitions import (
    _elapsed_seconds,
    _ensure_state_metadata,
    _question_answer_counts,
    _question_state,
)


class SessionSnapshotProjector:
    """Build the stable public session snapshot from domain state."""

    def project(self, state: InterviewState) -> dict[str, Any]:
        _ensure_state_metadata(state)
        current_question = (
            None
            if state["status"] == "finished"
            else get_current_question(state)
        )
        questions = [
            {
                **question.model_dump(),
                "state": _question_state(state, index),
            }
            for index, question in enumerate(state["plan"].questions)
        ]
        answer_counts = _question_answer_counts(state)
        return {
            "session_id": state["session_id"],
            "status": state["status"],
            "phase": state["phase"],
            "phase_status": state["phase_status"],
            "review_status": state["review_status"],
            "current_index": state["current_index"],
            "total_questions": len(state["plan"].questions),
            "completed_questions": (
                answer_counts["answered"] + answer_counts["skipped"]
            ),
            "answered_questions": answer_counts["answered"],
            "skipped_questions": answer_counts["skipped"],
            "unanswered_questions": answer_counts["unanswered"],
            "started_at": state["started_at"],
            "finished_at": state["finished_at"],
            "elapsed_seconds": _elapsed_seconds(state),
            "estimated_remaining_seconds": (
                answer_counts["pending_or_current"] * 6 * 60
            ),
            "state_version": state["state_version"],
            "checkpoint_version": state["checkpoint_version"],
            "last_checkpoint_at": state["last_checkpoint_at"],
            "last_command_id": state["last_command_id"],
            "workflow_engine": state.get("workflow_engine", "legacy"),
            "graph_schema_version": state.get("graph_schema_version"),
            "memory_policy_version": state["memory_policy_version"],
            "deletion_status": state.get("deletion_status", "active"),
            **interview_assistance_metadata(state),
            "job_tags": list(state["job_tags"]),
            "current_question": (
                current_question.model_dump() if current_question else None
            ),
            "questions": questions,
            "messages": [
                {
                    "role": message["role"],
                    "content": message["content"],
                    "question_id": message["question_id"],
                }
                for message in state["messages"]
            ],
        }


def interview_assistance_metadata(
    state: dict[str, Any],
    *,
    context_route: str | None = None,
    policy_version: str | None = None,
) -> dict[str, Any]:
    route = context_route or state.get("context_route") or "deterministic"
    resolved_policy = (
        policy_version
        or state.get("memory_policy_version")
        or "deterministic-v1"
    )
    assistance_mode = "full"
    user_notice_required = False

    plan = state.get("plan")
    prep_context = getattr(plan, "prep_context", None)
    if getattr(prep_context, "knowledge_status", None) == "degraded":
        assistance_mode = "reduced"

    messages = list(state.get("messages") or [])
    last_interviewer = next(
        (
            message
            for message in reversed(messages)
            if message.get("role") == "interviewer"
        ),
        None,
    )
    if last_interviewer is not None and plan is not None:
        template_followups = {
            fallback_followup(question.focus)
            for question in plan.questions
        }
        if last_interviewer.get("content") in template_followups:
            assistance_mode = "basic"
            user_notice_required = True

    return {
        "context_route": route,
        "assistance_mode": assistance_mode,
        "user_notice_required": user_notice_required,
        "policy_version": resolved_policy,
    }


__all__ = ["SessionSnapshotProjector", "interview_assistance_metadata"]
