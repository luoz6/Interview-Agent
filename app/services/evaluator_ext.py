import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.graphs.interview_state import InterviewState
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    correlation_id_from_plan,
)
from app.services.evaluator import (
    _apply_answer_state_overrides,
    build_evaluation_chunks,
    build_fallback_report,
)
from app.services.llm import InterviewLLM
from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.report import (
    FeedbackReference,
    InterviewReport,
    ReportGenerationFailed,
    ReportOutputFormatError,
    ReportProgress,
)
from app.services.vector_store import KnowledgeChunk, KnowledgeSearchStore

logger = logging.getLogger(__name__)


@dataclass
class ReviewerReferenceResolution:
    references: list[KnowledgeChunk | dict] = field(default_factory=list)
    retrieval_path: str = "legacy_semantic_search"
    degraded_reason: str | None = None


def resolve_reviewer_references(
    state: InterviewState,
    chunk,
    vector_store: KnowledgeSearchStore,
) -> ReviewerReferenceResolution:
    context = state["plan"].prep_context
    if context is not None and context.schema_version == "v2":
        binding = KnowledgeBindingResolver(vector_store).resolve(
            state["plan"],
            chunk.question_id,
        )
        return ReviewerReferenceResolution(
            references=list(binding.references),
            retrieval_path=binding.retrieval_path,
            degraded_reason=binding.degraded_reason,
        )

    references = vector_store.search(
        ExpertShadowEvaluator._build_query_text(
            chunk.question_text,
            chunk.focus,
            chunk.messages,
        ),
        job_tags=state["job_tags"],
        source_types=["theory", "expert_benchmark"],
        limit=5,
    )
    return ReviewerReferenceResolution(
        references=list(references),
        retrieval_path="legacy_semantic_search",
    )


class ExpertShadowEvaluator:
    def __init__(
        self,
        llm: InterviewLLM,
        vector_store: KnowledgeSearchStore,
        execution_runner: AgentExecutionRunner | None = None,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store
        self._execution_runner = execution_runner or AgentExecutionRunner()
        self.last_retrieval_by_question: dict[str, dict] = {}

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
        self.last_retrieval_by_question = {}
        for chunk in chunks:
            try:
                retrieval = resolve_reviewer_references(
                    state,
                    chunk,
                    self._vector_store,
                )
            except Exception as exc:
                raise ReportGenerationFailed("pgvector knowledge store is unavailable") from exc

            reference_dicts = [
                self._reference_to_dict(reference)
                for reference in retrieval.references
            ]
            self.last_retrieval_by_question[chunk.question_id] = {
                "retrieval_path": retrieval.retrieval_path,
                "degraded_reason": retrieval.degraded_reason,
                "evidence_content_sha256": {
                    reference["chunk_id"]: reference.get("metadata", {}).get(
                        "content_sha256"
                    )
                    for reference in reference_dicts
                    if reference.get("chunk_id")
                    and reference.get("metadata", {}).get("content_sha256")
                },
            }
            evaluation_items.append(
                {
                    "question_id": chunk.question_id,
                    "question_text": chunk.question_text,
                    "question_kind": chunk.question_kind,
                    "focus": chunk.focus,
                    "messages": chunk.model_dump()["messages"],
                    "scoring_references": reference_dicts,
                    "answer_references": reference_dicts,
                    "retrieval_path": retrieval.retrieval_path,
                    "degraded_reason": retrieval.degraded_reason,
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
            from app.agents.report_coach import ReportCoachAgent

            command_id = state.get("last_command_id")
            evidence_ids = [
                reference["chunk_id"]
                for item in evaluation_items
                for reference in item.get("scoring_references", [])
                if reference.get("chunk_id")
            ]
            report = ReportCoachAgent(
                llm=self._llm,
                execution_runner=self._execution_runner,
            ).generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
                execution_context=AgentExecutionContext(
                    correlation_id=correlation_id_from_plan(
                        state["plan"],
                        session_id=state["session_id"],
                    ),
                    causation_id=command_id,
                    agent="report_coach",
                    operation="generate_full_session_report",
                    phase="review",
                    session_id=state["session_id"],
                    state_version=state["state_version"],
                    command_id=command_id,
                    evidence_ids=evidence_ids,
                ),
                trace_metadata={
                    "question_count": len(chunks),
                    "report_path": "full_session",
                },
            )
            report = _apply_answer_state_overrides(report, chunks)
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
            report = _apply_answer_state_overrides(report, chunks)

        report = _enforce_v2_report_references(
            report,
            state["plan"],
            evaluation_items,
        )

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


def _enforce_v2_report_references(
    report: InterviewReport,
    plan,
    evaluation_items: list[dict],
) -> InterviewReport:
    context = plan.prep_context
    if context is None or context.schema_version != "v2":
        return report
    public_evidence_by_id = {
        reference.evidence_id: reference
        for reference in context.evidence_refs
    }
    trusted_by_question = {
        item["question_id"]: [
            reference
            for reference in item.get("scoring_references", [])
            if reference.get("chunk_id")
        ]
        for item in evaluation_items
    }
    feedbacks = []
    for feedback in report.feedbacks:
        references = []
        seen_ids: set[str] = set()
        for source in trusted_by_question.get(feedback.question_id, []):
            evidence_id = source["chunk_id"]
            if evidence_id in seen_ids:
                continue
            seen_ids.add(evidence_id)
            public_evidence = public_evidence_by_id.get(evidence_id)
            references.append(
                FeedbackReference(
                    chunk_id=evidence_id,
                    title=(
                        public_evidence.title
                        if public_evidence is not None
                        else source.get("title") or evidence_id
                    ),
                    source_type=(
                        public_evidence.source_type
                        if public_evidence is not None
                        else source.get("source_type") or "knowledge"
                    ),
                    excerpt=(
                        public_evidence.candidate_summary
                        if public_evidence is not None
                        else ""
                    ),
                )
            )
        feedbacks.append(feedback.model_copy(update={"references": references}))
    return report.model_copy(update={"feedbacks": feedbacks})
