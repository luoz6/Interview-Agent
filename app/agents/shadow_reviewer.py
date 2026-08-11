from collections.abc import Callable

from app.graphs.interview_state import InterviewState
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.llm import InterviewLLM
from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.services.report import InterviewReport, ReportProgress
from app.adapters.pgvector.repository import KnowledgeSearchStore
from app.services.context_runtime import ContextRuntime


class ShadowReviewerAgent:
    def __init__(
        self,
        *,
        llm: InterviewLLM,
        vector_store: KnowledgeSearchStore,
        execution_runner: AgentExecutionRunner | None = None,
        context_runtime: ContextRuntime | None = None,
        reference_transform: Callable | None = None,
    ) -> None:
        self.llm = llm
        self.vector_store = vector_store
        self._evaluator = ExpertShadowEvaluator(
            llm=llm,
            vector_store=vector_store,
            context_runtime=context_runtime,
            reference_transform=reference_transform,
        )
        self._execution_runner = execution_runner or AgentExecutionRunner()

    @property
    def last_retrieval_by_question(self) -> dict[str, dict]:
        return self._evaluator.last_retrieval_by_question

    def evaluate(
        self,
        state: InterviewState,
        on_progress: Callable[[ReportProgress], None] | None = None,
    ) -> InterviewReport:
        return self._evaluator.evaluate(state, on_progress=on_progress)

    def evaluate_attempt(
        self,
        state: InterviewState,
        *,
        execution_context: AgentExecutionContext,
        on_progress: Callable[[ReportProgress], None] | None = None,
    ) -> InterviewReport:
        return self._execution_runner.run(
            execution_context,
            lambda: self._evaluator.evaluate(state, on_progress=on_progress),
            fallback=None,
        )
