import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session_plan_binding import (
    SessionPlanBinding,
    legacy_session_plan_binding,
)


WorkflowEngine = Literal["legacy", "langgraph-v1", "langgraph-v2"]
MemoryPolicyVersion = Literal[
    "deterministic-v1",
    "question-conversation-v1",
    "question-memory-v1",
]
SUPPORTED_MEMORY_POLICY_VERSIONS = frozenset(
    {
        "deterministic-v1",
        "question-conversation-v1",
        "question-memory-v1",
    }
)
SUPPORTED_INTERVIEW_GRAPH_VERSIONS = frozenset(
    {"langgraph-v1", "langgraph-v2"}
)


def is_durable_interview_version(value: str | None) -> bool:
    return value in SUPPORTED_INTERVIEW_GRAPH_VERSIONS


class InterviewMessage(TypedDict):
    role: Literal["interviewer", "candidate"]
    content: str
    question_id: str | None


class InterviewDecision(TypedDict, total=False):
    action: Literal["follow_up", "next_question", "finish"]
    follow_up: str | None
    reason: str | None


class InterviewState(TypedDict):
    session_id: str
    plan: InterviewPlan
    current_index: int
    messages: list[InterviewMessage]
    decision: InterviewDecision | None
    decision_id: str | None
    decision_action: Literal["follow_up", "next_question"] | None
    decision_reason_code: str | None
    decision_gap_type: str | None
    decision_gap_summary: str | None
    followup_policy_version: Literal["fixed_v1", "adaptive_v1"]
    current_followup_count: int
    closed_gap_ids: list[str]
    active_gap_id: str | None
    termination_reason_code: str | None
    termination_diagnostic: dict[str, Any] | None
    pending_output: str | None
    status: Literal["active", "finished"]
    phase: Literal["prep", "interview", "review"]
    phase_status: Literal["pending", "active", "completed", "failed"]
    review_status: Literal["idle", "processing", "completed", "failed"]
    job_description: str
    resume_text: str
    job_tags: list[str]
    skipped_question_ids: list[str]
    started_at: str
    finished_at: str | None
    state_version: int
    checkpoint_version: int
    last_checkpoint_at: str | None
    last_command_id: str | None
    workflow_engine: WorkflowEngine
    graph_schema_version: str | None
    projection_sha256: str | None
    memory_policy_version: MemoryPolicyVersion
    deletion_status: Literal["active", "deleting"]
    plan_origin: Literal["plan_revision", "legacy_session_snapshot"]
    plan_revision_id: str | None
    plan_family_id: str | None
    revision: int | None
    plan_sha256: str
    configuration_snapshot: dict[str, Any] | None
    plan_snapshot: dict[str, Any]
    principal_memory_mode: Literal["inherit", "ignore"]
    owner_principal_id: str | None


def latest_candidate_answer_for_question(
    state: Mapping[str, Any],
    question_id: str,
) -> str:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return ""
    return next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if isinstance(message, Mapping)
            and message.get("role") == "candidate"
            and message.get("question_id") == question_id
        ),
        "",
    )


def choose_workflow_engine(
    session_id: str,
    *,
    runtime_store: str,
    runtime_enabled: bool,
    rollout_percent: int,
    durable_version: str = "langgraph-v1",
) -> WorkflowEngine:
    if durable_version not in SUPPORTED_INTERVIEW_GRAPH_VERSIONS:
        raise ValueError("unsupported durable interview graph version")
    if runtime_store != "postgres" or not runtime_enabled or rollout_percent == 0:
        return "legacy"
    bucket = int(
        hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8],
        16,
    ) % 100
    return durable_version if bucket < rollout_percent else "legacy"  # type: ignore[return-value]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_initial_state(
    session_id: str,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
    memory_policy_version: MemoryPolicyVersion = "deterministic-v1",
    plan_binding: SessionPlanBinding | None = None,
) -> InterviewState:
    if memory_policy_version not in SUPPORTED_MEMORY_POLICY_VERSIONS:
        raise ValueError("unsupported interview memory policy version")
    first_question = plan.questions[0] if plan.questions else None
    first_output = (
        first_question.prompt
        if first_question
        else "Interview finished because the plan is empty."
    )
    now = utc_now_iso()
    binding = plan_binding or legacy_session_plan_binding(plan)
    return {
        "session_id": session_id,
        "plan": plan,
        "current_index": 0,
        "messages": [
            {
                "role": "interviewer",
                "content": first_output,
                "question_id": first_question.id if first_question else None,
            }
        ],
        "decision": None,
        "decision_id": None,
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
        "termination_reason_code": None,
        "termination_diagnostic": None,
        "pending_output": first_output,
        "status": "active" if first_question else "finished",
        "phase": "interview",
        "phase_status": "active" if first_question else "completed",
        "review_status": "idle",
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": job_tags,
        "skipped_question_ids": [],
        "started_at": now,
        "finished_at": now if first_question is None else None,
        "state_version": 1,
        "checkpoint_version": 1,
        "last_checkpoint_at": now,
        "last_command_id": None,
        "workflow_engine": "legacy",
        "graph_schema_version": None,
        "projection_sha256": None,
        "memory_policy_version": memory_policy_version,
        "deletion_status": "active",
        **binding.model_dump(mode="json"),
    }


def default_memory_policy_for_engine(
    engine: WorkflowEngine,
) -> MemoryPolicyVersion:
    return (
        "question-conversation-v1"
        if engine == "langgraph-v2"
        else "deterministic-v1"
    )


def get_current_question(state: InterviewState) -> InterviewQuestion | None:
    current_index = state["current_index"]
    questions = state["plan"].questions
    if current_index >= len(questions):
        return None
    return questions[current_index]


def count_candidate_answers_for_question(
    state: InterviewState,
    question_id: str,
) -> int:
    return sum(
        1
        for message in state["messages"]
        if message["role"] == "candidate" and message["question_id"] == question_id
    )
