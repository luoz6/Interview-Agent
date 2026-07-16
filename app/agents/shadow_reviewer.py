from collections.abc import Callable

from app.graphs.interview_state import InterviewState
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.llm import InterviewLLM
from app.services.report import InterviewReport, ReportProgress
from app.services.vector_store import KnowledgeSearchStore


class ShadowReviewerAgent:
    def __init__(
        self,
        *,
        llm: InterviewLLM,
        vector_store: KnowledgeSearchStore,
    ) -> None:
        self.llm = llm
        self.vector_store = vector_store
        self._evaluator = ExpertShadowEvaluator(
            llm=llm,
            vector_store=vector_store,
        )

    @property
    def last_retrieval_by_question(self) -> dict[str, dict]:
        return self._evaluator.last_retrieval_by_question

    def evaluate(
        self,
        state: InterviewState,
        on_progress: Callable[[ReportProgress], None] | None = None,
    ) -> InterviewReport:
        return self._evaluator.evaluate(state, on_progress=on_progress)
