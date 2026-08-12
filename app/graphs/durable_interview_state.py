from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from app.graphs.interview_state import InterviewMessage
from app.services.prep import InterviewPlan
from app.services.session_plan_binding import (
    SessionPlanBinding,
    legacy_session_plan_binding,
)


class DurableQuestionSnapshot(BaseModel):
    id: str
    kind: str
    prompt: str
    focus: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_sha256: dict[str, str] = Field(default_factory=dict)


class DurablePlanSnapshot(BaseModel):
    title: str
    corpus_manifest_sha256: str | None = None
    questions: list[DurableQuestionSnapshot]

    @classmethod
    def from_plan(cls, plan: InterviewPlan) -> "DurablePlanSnapshot":
        context = plan.prep_context
        references = (
            {
                reference.evidence_id: reference.content_sha256
                for reference in context.evidence_refs
            }
            if context is not None
            else {}
        )
        evidence_ids_by_question = (
            {
                hint.question_id: list(hint.evidence_ids)
                for hint in context.question_hints
            }
            if context is not None
            else {}
        )
        manifest_sha256 = (
            context.binding_snapshot.corpus_manifest_sha256
            if context is not None and context.binding_snapshot is not None
            else None
        )
        return cls(
            title=plan.title,
            corpus_manifest_sha256=manifest_sha256,
            questions=[
                DurableQuestionSnapshot(
                    id=question.id,
                    kind=question.kind,
                    prompt=question.prompt,
                    focus=question.focus,
                    evidence_ids=evidence_ids_by_question.get(question.id, []),
                    evidence_sha256={
                        evidence_id: references[evidence_id]
                        for evidence_id in evidence_ids_by_question.get(
                            question.id, []
                        )
                        if evidence_id in references
                    },
                )
                for question in plan.questions
            ],
        )


class DurableInterviewState(TypedDict):
    session_id: str
    workflow_engine: Literal["langgraph-v1"]
    graph_schema_version: Literal["langgraph-v1"]
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
    plan_origin: Literal["plan_revision", "legacy_session_snapshot"]
    plan_revision_id: str | None
    plan_family_id: str | None
    revision: int | None
    plan_sha256: str
    configuration_snapshot: dict[str, Any] | None
    immutable_plan_snapshot: dict[str, Any]
    principal_memory_mode: Literal["inherit", "ignore"]


def make_durable_initial_state(
    session_id: str,
    plan: InterviewPlan,
    plan_binding: SessionPlanBinding | None = None,
) -> DurableInterviewState:
    snapshot = DurablePlanSnapshot.from_plan(plan)
    binding = plan_binding or legacy_session_plan_binding(plan)
    first = snapshot.questions[0] if snapshot.questions else None
    return {
        "session_id": session_id,
        "workflow_engine": "langgraph-v1",
        "graph_schema_version": "langgraph-v1",
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
        "plan_origin": binding.plan_origin,
        "plan_revision_id": binding.plan_revision_id,
        "plan_family_id": binding.plan_family_id,
        "revision": binding.revision,
        "plan_sha256": binding.plan_sha256,
        "configuration_snapshot": binding.configuration_snapshot,
        "immutable_plan_snapshot": binding.plan_snapshot,
        "principal_memory_mode": binding.principal_memory_mode,
    }
