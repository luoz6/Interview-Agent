from __future__ import annotations

from collections.abc import Iterator

from app.a2a.invocation.a2a import A2AAgentInvoker
from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.contracts.errors import A2AAgentError
from app.a2a.invocation.context import InvocationContext
from app.services.agent_runtime import AgentExecutionContext


class ExaminerAgentBridge:
    """Examiner-compatible bridge backed by an A2A AgentInvocationPort."""

    def __init__(self, invoker: LocalAgentInvoker | A2AAgentInvoker) -> None:
        self.invoker = invoker

    def generate_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
        execution_context: AgentExecutionContext | None = None,
        question_id: str = "",
        gap_id: str | None = None,
        reason_code: str = "gap",
        policy_version: str = "adaptive_v1",
        evidence_ids: list[str] | None = None,
    ) -> str:
        resolved_question_id = question_id or getattr(
            execution_context,
            "question_id",
            "",
        )
        if not resolved_question_id:
            raise A2AAgentError(
                code="domain_validation_failed",
                retryable=False,
                terminal=True,
                fallback_allowed=False,
                public_message="Examiner follow-up requires a question id.",
                internal_reason="execution_context.question_id is required",
            )
        resolved_evidence_ids = evidence_ids or list(
            getattr(execution_context, "evidence_ids", []) or []
        )
        artifact = self.invoker.invoke(
            agent_id="interview-examiner",
            skill="generate-followup",
            request={
                "question_id": resolved_question_id,
                "gap_id": gap_id,
                "context": context,
                "focus": focus,
                "reason_code": reason_code,
                "policy_version": policy_version,
                "evidence_ids": resolved_evidence_ids,
            },
            invocation_context=InvocationContext.from_execution_context(
                execution_context
            ),
            execution_context=execution_context,
        )
        return artifact.followup_text

    def stream_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
        execution_context: AgentExecutionContext | None = None,
    ) -> Iterator[str]:
        yield self.generate_followup(
            context=context,
            focus=focus,
            execution_context=execution_context,
        )
