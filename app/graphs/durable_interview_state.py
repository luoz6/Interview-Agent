from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from app.graphs.interview_state import InterviewMessage
from app.services.prep import InterviewPlan


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


def make_durable_initial_state(
    session_id: str,
    plan: InterviewPlan,
) -> DurableInterviewState:
    snapshot = DurablePlanSnapshot.from_plan(plan)
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
    }
