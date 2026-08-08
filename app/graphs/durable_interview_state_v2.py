from __future__ import annotations

from typing import Any, Literal, TypedDict

from app.graphs.durable_interview_state import DurablePlanSnapshot
from app.graphs.interview_state import InterviewMessage, MemoryPolicyVersion
from app.services.prep import InterviewPlan
from app.services.session_plan_binding import (
    SessionPlanBinding,
    legacy_session_plan_binding,
)


class DurableInterviewStateV2(TypedDict):
    session_id: str
    workflow_engine: Literal["langgraph-v2"]
    graph_schema_version: Literal["langgraph-v2"]
    plan_snapshot: dict
    current_index: int
    messages: list[InterviewMessage]
    skipped_question_ids: list[str]
    interview_status: Literal["active", "finished"]
    state_version: int
    last_command_id: str | None
    active_command_id: str | None
    active_decision_id: str | None
    decision_action: Literal["follow_up", "next_question"] | None
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
    generation_outcome: Literal[
        "completed", "retryable", "terminal"
    ] | None
    generated_text: str | None
    active_context_artifact_ref: str | None
    active_context_artifact_sha256: str | None
    active_context_artifact_type: str | None
    active_context_policy_version: str | None
    context_route: Literal[
        "deterministic",
        "artifact_reused",
        "artifact_created",
        "artifact_fallback",
        "memory_index_retrieved",
        "memory_index_empty",
    ] | None
    memory_policy_version: MemoryPolicyVersion
    plan_origin: Literal["plan_revision", "legacy_session_snapshot"]
    plan_revision_id: str | None
    plan_family_id: str | None
    revision: int | None
    plan_sha256: str
    configuration_snapshot: dict[str, Any] | None
    immutable_plan_snapshot: dict[str, Any]


def make_durable_initial_state_v2(
    session_id: str,
    plan: InterviewPlan,
    memory_policy_version: MemoryPolicyVersion = "question-conversation-v1",
    plan_binding: SessionPlanBinding | None = None,
) -> DurableInterviewStateV2:
    snapshot = DurablePlanSnapshot.from_plan(plan)
    binding = plan_binding or legacy_session_plan_binding(plan)
    first = snapshot.questions[0] if snapshot.questions else None
    return {
        "session_id": session_id,
        "workflow_engine": "langgraph-v2",
        "graph_schema_version": "langgraph-v2",
        "plan_snapshot": snapshot.model_dump(mode="json"),
        "current_index": 0,
        "messages": (
            [
                {
                    "role": "interviewer",
                    "content": first.prompt,
                    "question_id": first.id,
                }
            ]
            if first is not None
            else []
        ),
        "skipped_question_ids": [],
        "interview_status": "active" if first is not None else "finished",
        "state_version": 0,
        "last_command_id": None,
        "active_command_id": None,
        "active_decision_id": None,
        "decision_action": None,
        "decision_reason_code": None,
        "decision_gap_type": None,
        "decision_gap_summary": None,
        "followup_policy_version": (
            (binding.configuration_snapshot or {}).get(
                "followup_policy_version", "fixed_v1"
            )
        ),
        "current_followup_count": 0,
        "closed_gap_ids": [],
        "active_gap_id": None,
        "decision_outcome": None,
        "decision_prompt_version": None,
        "decision_prompt_sha256": None,
        "generation_id": None,
        "generation_attempt": 1,
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
        "generation_outcome": None,
        "generated_text": None,
        "active_context_artifact_ref": None,
        "active_context_artifact_sha256": None,
        "active_context_artifact_type": None,
        "active_context_policy_version": None,
        "context_route": None,
        "memory_policy_version": memory_policy_version,
        "plan_origin": binding.plan_origin,
        "plan_revision_id": binding.plan_revision_id,
        "plan_family_id": binding.plan_family_id,
        "revision": binding.revision,
        "plan_sha256": binding.plan_sha256,
        "configuration_snapshot": binding.configuration_snapshot,
        "immutable_plan_snapshot": binding.plan_snapshot,
    }
