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
    generation_id: str | None
    generation_attempt: int
    expected_retry_attempt: int | None
    retry_resume_attempt: int | None
    retry_validation: Literal["accepted", "stale"] | None
    next_retry_at: str | None
    last_error_code: str | None
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
        "generation_id": None,
        "generation_attempt": 1,
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "retry_validation": None,
        "next_retry_at": None,
        "last_error_code": None,
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
