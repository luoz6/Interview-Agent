from uuid import uuid4

from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.services.llm import InterviewLLM
from app.services.report import InterviewReport


class ReportCoachAgent:
    def __init__(
        self,
        llm: InterviewLLM | None = None,
        *,
        execution_runner: AgentExecutionRunner | None = None,
    ) -> None:
        self.llm = llm
        self._execution_runner = execution_runner or AgentExecutionRunner()

    def generate_report(
        self,
        *,
        plan,
        evaluation_items: list[dict],
        session_id: str,
        execution_context: AgentExecutionContext | None = None,
        trace_metadata: dict | None = None,
    ) -> InterviewReport:
        llm = self.llm or self._default_llm()
        context = execution_context or AgentExecutionContext(
            correlation_id=f"coach-{uuid4().hex}",
            agent="report_coach",
            operation="generate_report",
            phase="review",
            session_id=session_id,
        )
        return self._execution_runner.run(
            context,
            lambda: llm.generate_report(
                plan=plan,
                evaluation_items=evaluation_items,
                session_id=session_id,
            ),
            metadata=lambda report: {
                "feedback_count": len(report.feedbacks),
                **dict(trace_metadata or {}),
            },
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
