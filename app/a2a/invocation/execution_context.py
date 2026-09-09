from __future__ import annotations

from typing import Any

from app.a2a.invocation.context import InvocationContext
from app.services.agent_runtime import AgentExecutionContext


_AGENT_NAME_MAP = {
    "interview-examiner": "examiner",
    "knowledge-and-grounding": "knowledge",
    "interview-reviewer": "shadow_reviewer",
    "report-coach": "report_coach",
}

_SKILL_OPERATION_MAP = {
    "generate-followup": "generate_followup",
    "generate-interview-plan": "generate_plan",
    "evaluate-answer": "evaluate_answer",
    "evaluate-interview": "evaluate_interview",
    "generate-report": "generate_report",
}

_PHASE_MAP = {
    "interview-examiner": "interview",
    "knowledge-and-grounding": "prep",
    "interview-reviewer": "review",
    "report-coach": "review",
}


def build_agent_execution_context(
    *,
    agent_id: str,
    skill: str,
    invocation_context: InvocationContext | None,
    request: dict[str, Any],
) -> AgentExecutionContext:
    context = invocation_context or InvocationContext()
    correlation_id = context.correlation_id or context.context_id or "a2a-standalone"
    agent_name = _AGENT_NAME_MAP.get(agent_id, "orchestrator")
    operation = _SKILL_OPERATION_MAP.get(skill, skill)
    phase = _PHASE_MAP.get(agent_id, "review")
    session_id = context.session_id or (
        context.context_id if phase in {"interview", "review"} else None
    )
    question_id = context.question_id or request.get("question_id")
    state_version = context.state_version
    evidence_ids = list(context.evidence_ids or [])
    if not question_id:
        question_id = None
    return AgentExecutionContext(
        correlation_id=correlation_id,
        agent=agent_name,
        operation=operation,
        phase=phase,
        session_id=session_id,
        question_id=question_id,
        state_version=state_version,
        causation_id=context.causation_id,
        parent_run_id=context.parent_run_id,
        command_id=context.command_id,
        evidence_ids=evidence_ids,
    )
