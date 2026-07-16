from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


AgentName = Literal[
    "orchestrator",
    "knowledge",
    "examiner",
    "shadow_reviewer",
    "report_coach",
]
AgentPhase = Literal["prep", "interview", "review"]
AgentRunStatus = Literal["completed", "degraded", "failed", "cancelled"]


class AgentExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent-runtime-v1"] = "agent-runtime-v1"
    run_id: str = Field(default_factory=lambda: f"agent-{uuid4().hex}")
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    agent: AgentName
    operation: str = Field(min_length=1)
    phase: AgentPhase
    session_id: str | None = None
    question_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    command_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def deduplicate_evidence_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in value if item))


class AgentRunRecord(AgentExecutionContext):
    status: AgentRunStatus
    started_at: str
    finished_at: str
    latency_ms: float = Field(ge=0)
    fallback_reason: str | None = None
    error_code: str | None = None
    output_type: str | None = None
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


def correlation_id_from_plan(plan, *, session_id: str | None = None) -> str:
    prep_context = getattr(plan, "prep_context", None)
    snapshot = getattr(prep_context, "binding_snapshot", None)
    prep_run_id = getattr(snapshot, "prep_run_id", None)
    if isinstance(prep_run_id, str) and prep_run_id:
        return prep_run_id
    if isinstance(session_id, str) and session_id:
        return session_id
    return f"prep-{uuid4().hex}"


def evidence_ids_for_question(plan, question_id: str | None) -> list[str]:
    if not question_id:
        return []
    prep_context = getattr(plan, "prep_context", None)
    hints = getattr(prep_context, "question_hints", []) if prep_context else []
    for hint in hints:
        if hint.question_id == question_id:
            return list(dict.fromkeys(hint.evidence_ids))
    return []
