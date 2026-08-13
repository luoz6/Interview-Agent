from uuid import uuid4

from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.services.llm import InterviewLLM
from app.services.report import InterviewReport
from app.services.report_answer_guidance import apply_report_answer_guidance


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
        report = self._execution_runner.run(
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
        return apply_report_answer_guidance(report)

    def generate_report_attempt(
        self,
        *,
        plan,
        evaluation_items: list[dict],
        session_id: str,
        execution_context: AgentExecutionContext,
        trace_metadata: dict | None = None,
    ) -> InterviewReport:
        llm = self.llm or self._default_llm()
        report = self._execution_runner.run(
            execution_context,
            lambda: llm.generate_report(
                plan=plan,
                evaluation_items=evaluation_items,
                session_id=session_id,
            ),
            fallback=None,
            metadata=lambda report: {
                "feedback_count": len(report.feedbacks),
                **dict(trace_metadata or {}),
            },
        )
        return apply_report_answer_guidance(report)

    def repair_report_attempt(
        self,
        *,
        plan,
        evaluation_items: list[dict],
        session_id: str,
        issues: list[dict],
        prior_report: InterviewReport,
        execution_context: AgentExecutionContext,
    ) -> InterviewReport:
        if not issues:
            raise ValueError("report quality repair requires explicit issues")
        repair_items = [
            *evaluation_items,
            {
                "source": "quality_repair",
                "quality_issues": list(issues),
                "prior_report": prior_report.model_dump(mode="json"),
            },
        ]
        return self.generate_report_attempt(
            plan=plan,
            evaluation_items=repair_items,
            session_id=session_id,
            execution_context=execution_context,
            trace_metadata={"quality_repair": True, "issue_count": len(issues)},
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
