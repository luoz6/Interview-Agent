from __future__ import annotations

from typing import Any, Literal, TypedDict

from app.services.session_plan_binding import SessionPlanBinding


class DurableInterviewStateV3(TypedDict):
    session_id: str
    workflow_engine: Literal["langgraph-v3"]
    graph_schema_version: Literal["langgraph-v3"]
    plan_snapshot: dict[str, Any]
    current_index: int
    messages: list[dict[str, Any]]
    skipped_question_ids: list[str]
    interview_status: Literal[
        "preparing_first_question", "active", "finished"
    ]
    state_version: int
    last_command_id: str | None
    active_command_id: str | None
    active_decision_id: str | None
    decision_action: Literal["follow_up", "next_question"] | None
    decision_answer_state: Literal[
        "complete", "partial", "incorrect", "off_topic", "empty"
    ] | None
    decision_reason_code: str | None
    decision_gap_type: str | None
    decision_gap_summary: str | None
    followup_policy_version: Literal["fixed_v1", "adaptive_v1"]
    current_followup_count: int
    closed_gap_ids: list[str]
    active_gap_id: str | None
    decision_outcome: Literal["pending", "completed"] | None
    decision_prompt_version: str | None
    decision_prompt_sha256: str | None
    generation_id: str | None
    generation_attempt: int
    generation_outcome: Literal["completed", "retryable", "terminal"] | None
    generated_text: str | None
    expected_retry_attempt: int | None
    retry_resume_attempt: int | None
    retry_validation: Literal["accepted", "stale"] | None
    next_retry_at: str | None
    last_error_code: str | None
    termination_reason_code: str | None
    termination_diagnostic: dict[str, Any] | None
    followup_guard_reason_code: str | None
    command_node_steps: int
    command_provider_invocations: int
    command_generation_entries: int
    command_generation_followup_count: int | None
    command_last_progress_hash: str | None
    command_last_progress_action: str | None
    command_repeat_count: int
    command_last_checkpoint_version: int
    command_type: Literal["answer", "skip", "finish"] | None
    command_outcome: Literal[
        "accepted", "duplicate", "conflict", "completed"
    ] | None
    configuration_snapshot: dict[str, Any] | None
    immutable_plan_snapshot: dict[str, Any]
    principal_memory_mode: Literal["inherit", "ignore"]
    job_description: str
    resume_text: str
    job_tags: list[str]
    current_rendered_question_id: str | None
    rendered_questions: dict[str, dict[str, Any]]
    pending_main_question_index: int | None
    question_generation_id: str | None
    question_generation_attempt: int
    question_generation_outcome: Literal["pending", "completed", "failed"] | None
    question_generation_reason_code: str | None
    question_generation_context_sha256: str | None
    question_generation_context: list[dict[str, str]] | None
    question_generation_result: dict[str, Any] | None


def make_durable_initial_state_v3(
    session_id: str,
    plan,
    *,
    plan_binding: SessionPlanBinding,
    job_description: str = "",
    resume_text: str = "",
    job_tags: list[str] | None = None,
) -> DurableInterviewStateV3:
    snapshot = plan_binding.plan_snapshot
    if snapshot.get("schema_version") != "interview-plan-v3":
        raise ValueError("langgraph-v3 requires interview-plan-v3")
    questions = snapshot.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("interview-plan-v3 requires intents")
    configuration = plan_binding.configuration_snapshot or {}
    return {
        "session_id": session_id,
        "workflow_engine": "langgraph-v3",
        "graph_schema_version": "langgraph-v3",
        "plan_snapshot": snapshot,
        "current_index": 0,
        "messages": [],
        "skipped_question_ids": [],
        "interview_status": "preparing_first_question",
        "state_version": 0,
        "last_command_id": None,
        "active_command_id": None,
        "active_decision_id": None,
        "decision_action": None,
        "decision_answer_state": None,
        "decision_reason_code": None,
        "decision_gap_type": None,
        "decision_gap_summary": None,
        "followup_policy_version": configuration.get(
            "followup_policy_version", "fixed_v1"
        ),
        "current_followup_count": 0,
        "closed_gap_ids": [],
        "active_gap_id": None,
        "decision_outcome": None,
        "decision_prompt_version": None,
        "decision_prompt_sha256": None,
        "generation_id": None,
        "generation_attempt": 1,
        "generation_outcome": None,
        "generated_text": None,
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "retry_validation": None,
        "next_retry_at": None,
        "last_error_code": None,
        "termination_reason_code": None,
        "termination_diagnostic": None,
        "followup_guard_reason_code": None,
        "command_node_steps": 0,
        "command_provider_invocations": 0,
        "command_generation_entries": 0,
        "command_generation_followup_count": None,
        "command_last_progress_hash": None,
        "command_last_progress_action": None,
        "command_repeat_count": 0,
        "command_last_checkpoint_version": 0,
        "command_type": None,
        "command_outcome": None,
        "configuration_snapshot": configuration,
        "immutable_plan_snapshot": snapshot,
        "principal_memory_mode": plan_binding.principal_memory_mode,
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": list(job_tags or []),
        "current_rendered_question_id": None,
        "rendered_questions": {},
        "pending_main_question_index": 0,
        "question_generation_id": None,
        "question_generation_attempt": 1,
        "question_generation_outcome": None,
        "question_generation_reason_code": None,
        "question_generation_context_sha256": None,
        "question_generation_context": None,
        "question_generation_result": None,
    }


__all__ = ["DurableInterviewStateV3", "make_durable_initial_state_v3"]
