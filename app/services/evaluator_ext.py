import logging
from collections.abc import Callable

from app.graphs.interview_state import InterviewState
from app.services.evaluator import build_evaluation_chunks, build_fallback_report
from app.services.llm import InterviewLLM
from app.services.report import (
    InterviewReport,
    ReportGenerationFailed,
    ReportOutputFormatError,
    ReportProgress,
)
from app.services.vector_store import KnowledgeChunk, KnowledgeSearchStore

logger = logging.getLogger(__name__)


class ExpertShadowEvaluator:
    def __init__(
        self,
        llm: InterviewLLM,
        vector_store: KnowledgeSearchStore,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store

    def evaluate(
        self,
        state: InterviewState,
        on_progress: Callable[[ReportProgress], None] | None = None,
    ) -> InterviewReport:
        chunks = build_evaluation_chunks(state)
        if on_progress is not None:
            on_progress(
                ReportProgress(
                    stage="retrieving",
                    percent=20,
                    message="Retrieving role-specific knowledge references.",
                )
            )

        evaluation_items: list[dict] = []
        for chunk in chunks:
            try:
                references = self._vector_store.search(
                    self._build_query_text(chunk.question_text, chunk.focus, chunk.messages),
                    job_tags=state["job_tags"],
                    source_types=["theory", "expert_benchmark"],
                    limit=5,
                )
            except Exception as exc:
                raise ReportGenerationFailed("pgvector knowledge store is unavailable") from exc

            reference_dicts = [self._reference_to_dict(reference) for reference in references]
            evaluation_items.append(
                {
                    "question_id": chunk.question_id,
                    "question_text": chunk.question_text,
                    "focus": chunk.focus,
                    "messages": chunk.model_dump()["messages"],
                    "scoring_references": reference_dicts,
                    "answer_references": reference_dicts,
                }
            )

        if on_progress is not None:
            on_progress(
                ReportProgress(
                    stage="analyzing",
                    percent=60,
                    message="Analyzing question-level dimension scores.",
                    current_question_id=chunks[0].question_id if chunks else None,
                )
            )

        try:
            report = self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
            )
        except ReportOutputFormatError as exc:
            logger.warning(
                "Falling back to heuristic interview report",
                extra={
                    "session_id": state["session_id"],
                    "reason": str(exc),
                    "question_count": len(chunks),
                },
            )
            report = build_fallback_report(state, chunks)

        if on_progress is not None:
            on_progress(
                ReportProgress(
                    stage="aggregating",
                    percent=80,
                    message="Aggregating overall expert scores.",
                )
            )
            on_progress(
                ReportProgress(
                    stage="completed",
                    percent=100,
                    message="Expert report completed.",
                )
            )
        return report

    @staticmethod
    def _build_query_text(
        question_text: str,
        focus: str,
        messages: list[dict[str, str]],
    ) -> str:
        message_text = " ".join(message["content"] for message in messages if message["content"])
        return f"{question_text}\n{focus}\n{message_text}"

    @staticmethod
    def _reference_to_dict(reference: KnowledgeChunk | dict) -> dict:
        if isinstance(reference, dict):
            return reference
        return reference.model_dump()
