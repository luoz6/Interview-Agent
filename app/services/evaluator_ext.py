import logging
import json
from hashlib import sha256
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
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.evidence import (
    EvidenceAvailability,
    EvidenceDecision,
    EvidenceRef,
    EvidenceSufficiency,
    ReviewEvidenceBinding,
)
from app.domain.knowledge.evidence_gate import (
    EvaluationSupportGate,
    RetrievalEvidenceGate,
)
from app.domain.knowledge.knowledge_unit import KnowledgeUnit
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalIntent,
    RetrievalResult,
    RetrievalTrace,
)
from app.domain.knowledge.rollout import KnowledgeEngineAssignment
from app.adapters.pgvector.repository import KnowledgeSearchStore
from app.services.context_budget import (
    QUESTION_REVIEW_CONTEXT_POLICY,
    context_enforcement_enabled,
)
from app.services.context_selection import (
    select_interview_messages,
    truncate_text_to_tokens,
)
from app.services.context_runtime import ContextRuntime, get_context_runtime

logger = logging.getLogger(__name__)


@dataclass
class ReviewerReferenceResolution:
    references: list[KnowledgeChunk | dict] = field(default_factory=list)
    retrieval_path: str = "legacy_semantic_search"
    degraded_reason: str | None = None
    replayed_evidence_ids: list[str] = field(default_factory=list)
    supplemental_evidence_ids: list[str] = field(default_factory=list)
    decision: EvidenceDecision | None = None
    evidence_binding_id: str | None = None
    review_binding: ReviewEvidenceBinding | None = None


def resolve_reviewer_references(
    state: InterviewState,
    chunk,
    vector_store: KnowledgeSearchStore,
    *,
    unit_resolver=None,
    support_gate: EvaluationSupportGate | None = None,
) -> ReviewerReferenceResolution:
    context = state["plan"].prep_context
    if context is not None and context.schema_version == "v2":
        binding = KnowledgeBindingResolver(vector_store).resolve(
            state["plan"],
            chunk.question_id,
        )
        if binding.retrieval_path == "bound_evidence_ids":
            replayed_references = list(binding.references)
            unit = (
                unit_resolver.resolve(replayed_references)
                if unit_resolver is not None
                else None
            )
            decision = _review_evidence_decision(
                replayed_references,
                unit=unit,
                evaluation_level=_evaluation_level(state, chunk),
                support_gate=support_gate,
            )
            supplemental_references: list = []
            targeted_unavailable = False
            if unit is not None and decision.sufficiency in {
                EvidenceSufficiency.WEAK,
                EvidenceSufficiency.INSUFFICIENT,
            }:
                try:
                    supplemental_references = _targeted_reviewer_search(
                        state, chunk, vector_store
                    )
                except Exception:
                    supplemental_references = []
                    targeted_unavailable = True
                references = _merge_references(
                    replayed_references,
                    supplemental_references,
                )
                if targeted_unavailable:
                    decision = (support_gate or EvaluationSupportGate()).decide(
                        [_as_chunk(reference) for reference in replayed_references],
                        unit,
                        evaluation_level=_evaluation_level(state, chunk),
                        availability=EvidenceAvailability.DEGRADED,
                    )
                    decision = _with_reason(
                        decision,
                        "supplemental_retrieval_unavailable",
                    )
                else:
                    decision = _review_evidence_decision(
                        references,
                        unit=unit,
                        evaluation_level=_evaluation_level(state, chunk),
                        support_gate=support_gate,
                    )
                decision = _with_reason(
                    decision,
                    "supplemental_retrieval_required",
                )
                retrieval_path = "bound_evidence_plus_targeted"
            else:
                references = replayed_references
                retrieval_path = "bound_evidence_ids"
            replayed_ids = [_reference_id(item) for item in replayed_references]
            supplemental_ids = [
                _reference_id(item) for item in supplemental_references
            ]
            review_binding = ReviewEvidenceBinding(
                binding_id=_review_binding_id(
                    state["session_id"],
                    chunk.question_id,
                    replayed_ids=replayed_ids,
                    supplemental_ids=supplemental_ids,
                ),
                parent_question_binding_id=(
                    binding.question_binding_id
                    or _legacy_question_binding_id(
                        state["session_id"], chunk.question_id
                    )
                ),
                replayed_evidence_ids=tuple(replayed_ids),
                supplemental_evidence_ids=tuple(supplemental_ids),
                supplemental_evidence_refs=tuple(
                    _evidence_ref(reference)
                    for reference in supplemental_references
                ),
                final_evidence_ids=tuple(
                    dict.fromkeys((*replayed_ids, *supplemental_ids))
                ),
                decision=decision,
            )
            return ReviewerReferenceResolution(
                references=references,
                retrieval_path=retrieval_path,
                degraded_reason=(
                    "supplemental_retrieval_unavailable"
                    if targeted_unavailable
                    else None
                ),
                replayed_evidence_ids=replayed_ids,
                supplemental_evidence_ids=supplemental_ids,
                decision=decision,
                evidence_binding_id=review_binding.binding_id,
                review_binding=review_binding,
            )

        targeted_unavailable = False
        try:
            references = _targeted_reviewer_search(state, chunk, vector_store)
        except Exception:
            references = []
            targeted_unavailable = True
        unit = (
            unit_resolver.resolve(references)
            if unit_resolver is not None and references
            else None
        )
        decision = _review_evidence_decision(
            references,
            unit=unit,
            evaluation_level=_evaluation_level(state, chunk),
            support_gate=support_gate,
            unavailable=targeted_unavailable,
        )
        decision = EvidenceDecision.model_validate(
            {
                **decision.model_dump(),
                "reason_codes": tuple(
                    dict.fromkeys(
                        (*decision.reason_codes, "supplemental_retrieval_required")
                    )
                ),
            }
        )
        supplemental_ids = [_reference_id(reference) for reference in references]
        parent_binding_id = binding.question_binding_id
        if parent_binding_id is None and not (
            context.binding_snapshot
            and context.binding_snapshot.question_evidence_bindings
        ):
            parent_binding_id = _legacy_question_binding_id(
                state["session_id"], chunk.question_id
            )
        review_binding = (
            ReviewEvidenceBinding(
                binding_id=_review_binding_id(
                    state["session_id"],
                    chunk.question_id,
                    supplemental_ids=supplemental_ids,
                ),
                parent_question_binding_id=parent_binding_id,
                supplemental_evidence_ids=tuple(supplemental_ids),
                supplemental_evidence_refs=tuple(
                    _evidence_ref(reference) for reference in references
                ),
                final_evidence_ids=tuple(supplemental_ids),
                decision=decision,
            )
            if parent_binding_id is not None
            else None
        )
        return ReviewerReferenceResolution(
            references=references,
            retrieval_path="targeted_retrieval",
            degraded_reason=binding.degraded_reason,
            supplemental_evidence_ids=supplemental_ids,
            decision=decision,
            evidence_binding_id=(review_binding.binding_id if review_binding else None),
            review_binding=review_binding,
        )

    references = _targeted_reviewer_search(state, chunk, vector_store)
    decision = _review_retrieval_decision(references)
    return ReviewerReferenceResolution(
        references=list(references),
        retrieval_path="legacy_semantic_search",
        supplemental_evidence_ids=[_reference_id(reference) for reference in references],
        decision=decision,
    )


def _targeted_reviewer_search(state, chunk, vector_store) -> list:
    if callable(getattr(vector_store, "search_runtime", None)):
        context = state["plan"].prep_context
        snapshot = context.binding_snapshot if context is not None else None
        raw_assignment = (
            snapshot.knowledge_engine_assignment if snapshot is not None else None
        )
        assignment = (
            raw_assignment
            if isinstance(raw_assignment, KnowledgeEngineAssignment)
            else KnowledgeEngineAssignment.model_validate(raw_assignment)
            if raw_assignment
            else None
        )
        outcome = vector_store.search_runtime(
            ExpertShadowEvaluator._build_query_text(
                chunk.question_text,
                chunk.focus,
                chunk.messages,
            ),
            intent=RetrievalIntent.QUESTION_REVIEW,
            job_tags=state["job_tags"],
            source_types=["theory", "expert_benchmark"],
            limit=5,
            session_id=state["session_id"],
            question_id=chunk.question_id,
            prep_run_id=(snapshot.prep_run_id if snapshot is not None else None),
            existing_assignment=assignment,
        )
        return list(outcome.result.selected_evidence)
    return list(
        vector_store.search(
            ExpertShadowEvaluator._build_query_text(
                chunk.question_text,
                chunk.focus,
                chunk.messages,
            ),
            job_tags=state["job_tags"],
            source_types=["theory", "expert_benchmark"],
            limit=5,
        )
    )


def _review_retrieval_decision(
    references: list,
    *,
    unavailable: bool = False,
) -> EvidenceDecision:
    chunks = [
        reference
        if isinstance(reference, KnowledgeChunk)
        else KnowledgeChunk.model_validate(reference)
        for reference in references
    ]
    availability = (
        RetrievalAvailability.UNAVAILABLE
        if unavailable
        else RetrievalAvailability.AVAILABLE
    )
    result = RetrievalResult(
        request_id="review-evidence-gate",
        availability=availability,
        selected_evidence=chunks,
        trace=RetrievalTrace(
            request_id="review-evidence-gate",
            profile_id="question-review",
            profile_version="v1",
            latency_ms=0,
        ),
        retrieval_engine_version="review-resolution-v1",
        profile_version="v1",
        latency_ms=0,
    )
    return RetrievalEvidenceGate().decide(result)


def _review_evidence_decision(
    references: list,
    *,
    unit: KnowledgeUnit | None,
    evaluation_level: str | None,
    support_gate: EvaluationSupportGate | None,
    unavailable: bool = False,
) -> EvidenceDecision:
    retrieval_decision = _review_retrieval_decision(
        references,
        unavailable=unavailable,
    )
    if (
        unit is None
        or unavailable
        or retrieval_decision.sufficiency
        != EvidenceSufficiency.NOT_EVALUATED
    ):
        return retrieval_decision
    chunks = [_as_chunk(reference) for reference in references]
    return (support_gate or EvaluationSupportGate()).decide(
        chunks,
        unit,
        evaluation_level=evaluation_level,
        availability=retrieval_decision.availability,
    )


def _evaluation_level(state: InterviewState, chunk) -> str | None:
    context = state["plan"].prep_context
    profile = context.role_profile if context is not None else None
    seniority = profile.seniority if profile is not None else ""
    if seniority:
        return {
            "principal": "advanced",
            "staff": "advanced",
            "lead": "advanced",
            "senior": "advanced",
            "mid": "intermediate",
            "junior": "beginner",
        }.get(seniority, seniority)
    focus = str(getattr(chunk, "focus", "")).casefold()
    return next(
        (
            level
            for level in ("principal", "staff", "lead", "senior", "advanced")
            if level in focus
        ),
        None,
    )


def _merge_references(replayed: list, supplemental: list) -> list:
    merged = []
    seen: set[str] = set()
    for reference in (*replayed, *supplemental):
        evidence_id = _reference_id(reference)
        if evidence_id and evidence_id not in seen:
            seen.add(evidence_id)
            merged.append(reference)
    return merged


def _with_reason(decision: EvidenceDecision, reason: str) -> EvidenceDecision:
    return EvidenceDecision.model_validate(
        {
            **decision.model_dump(),
            "reason_codes": tuple(
                dict.fromkeys((*decision.reason_codes, reason))
            ),
        }
    )


def _as_chunk(reference) -> KnowledgeChunk:
    return (
        reference
        if isinstance(reference, KnowledgeChunk)
        else KnowledgeChunk.model_validate(reference)
    )


def _evidence_ref(reference) -> EvidenceRef:
    return EvidenceRef.from_chunk(_as_chunk(reference))


def _reference_id(reference) -> str:
    if isinstance(reference, dict):
        return str(reference.get("chunk_id") or "")
    return str(reference.chunk_id)


def _legacy_question_binding_id(session_id: str, question_id: str) -> str:
    digest = sha256(f"{session_id}:{question_id}".encode("utf-8")).hexdigest()[:24]
    return f"question-binding-{digest}"


def _review_binding_id(
    session_id: str,
    question_id: str,
    *,
    replayed_ids: list[str] | None = None,
    supplemental_ids: list[str] | None = None,
) -> str:
    identity = ":".join(
        [
            session_id,
            question_id,
            ",".join(replayed_ids or []),
            ",".join(supplemental_ids or []),
        ]
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"review-binding-{digest}"


class ExpertShadowEvaluator:
    def __init__(
        self,
        llm: InterviewLLM,
        vector_store: KnowledgeSearchStore,
        execution_runner: AgentExecutionRunner | None = None,
        context_runtime: ContextRuntime | None = None,
        reference_transform: Callable | None = None,
        knowledge_unit_resolver=None,
        evaluation_support_gate: EvaluationSupportGate | None = None,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store
        self._execution_runner = execution_runner or AgentExecutionRunner()
        # Reuse the LLM's runtime when available and otherwise resolve only
        # when review enforcement is exercised. This preserves the legacy path
        # while production composition may inject a preflighted runtime.
        self._context_runtime = context_runtime or getattr(
            llm,
            "context_runtime",
            None,
        )
        self.last_retrieval_by_question: dict[str, dict] = {}
        self._reference_transform = reference_transform
        if knowledge_unit_resolver is None:
            from app.adapters.knowledge.pilot_unit_resolver import (
                default_knowledge_unit_resolver,
            )

            knowledge_unit_resolver = default_knowledge_unit_resolver()
        self._knowledge_unit_resolver = knowledge_unit_resolver
        self._evaluation_support_gate = (
            evaluation_support_gate or EvaluationSupportGate()
        )

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
                    unit_resolver=self._knowledge_unit_resolver,
                    support_gate=self._evaluation_support_gate,
                )
            except Exception as exc:
                raise ReportGenerationFailed("pgvector knowledge store is unavailable") from exc

            reference_dicts = [
                self._reference_to_dict(reference)
                for reference in retrieval.references
            ]
            effective_reference_dicts = (
                self._reference_transform(
                    state=state,
                    chunk=chunk,
                    references=reference_dicts,
                )
                if self._reference_transform is not None
                else reference_dicts
            )
            if context_enforcement_enabled(QUESTION_REVIEW_CONTEXT_POLICY.operation):
                bounded_messages, bounded_references = _budget_question_review_input(
                    chunk,
                    effective_reference_dicts,
                    context_runtime=self._context_runtime,
                )
            else:
                bounded_messages = chunk.model_dump()["messages"]
                bounded_references = effective_reference_dicts
            retrieval = _align_review_binding_with_scoring_input(
                retrieval,
                bounded_references,
            )
            final_evidence_ids = [
                _reference_id(reference) for reference in bounded_references
                if _reference_id(reference)
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
                "evaluation_confidence": (
                    retrieval.decision.evaluation_confidence.value
                    if retrieval.decision
                    else None
                ),
                "evidence_availability": (
                    retrieval.decision.availability.value
                    if retrieval.decision
                    else None
                ),
                "evidence_sufficiency": (
                    retrieval.decision.sufficiency.value
                    if retrieval.decision
                    else None
                ),
                "evidence_consistency": (
                    retrieval.decision.consistency.value
                    if retrieval.decision
                    else None
                ),
                "evidence_ids": final_evidence_ids,
                "gate_reason_codes": (
                    list(retrieval.decision.reason_codes)
                    if retrieval.decision
                    else []
                ),
                "evidence_binding_id": retrieval.evidence_binding_id,
                "review_evidence_binding": (
                    retrieval.review_binding.model_dump(mode="json")
                    if retrieval.review_binding
                    else None
                ),
            }
            evaluation_items.append(
                {
                    "question_id": chunk.question_id,
                    "question_text": chunk.question_text,
                    "question_kind": chunk.question_kind,
                    "focus": chunk.focus,
                    "messages": bounded_messages,
                    "scoring_references": bounded_references,
                    "answer_references": [],
                    "retrieval_path": retrieval.retrieval_path,
                    "degraded_reason": retrieval.degraded_reason,
                    "evidence_decision": (
                        retrieval.decision.model_dump(mode="json")
                        if retrieval.decision
                        else None
                    ),
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
                    state_version=state.get("state_version"),
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


def _budget_question_review_input(
    chunk,
    reference_dicts: list[dict],
    *,
    context_runtime: ContextRuntime | None = None,
):
    runtime = context_runtime or get_context_runtime()
    model = runtime.model_profile.model
    estimator = runtime.estimator_resolution.estimator
    messages, _ = select_interview_messages(
        chunk.messages,
        current_question_id=chunk.question_id,
        token_budget=9_000,
        max_single_message_tokens=(
            QUESTION_REVIEW_CONTEXT_POLICY.max_single_message_tokens
        ),
        estimator=estimator,
        model=model,
    )
    selected_references: list[dict] = []
    remaining = QUESTION_REVIEW_CONTEXT_POLICY.max_total_evidence_tokens
    for reference in reference_dicts[: QUESTION_REVIEW_CONTEXT_POLICY.max_evidence_items]:
        bounded = dict(reference)
        content = str(bounded.get("content", ""))
        if content:
            content, _ = truncate_text_to_tokens(
                content,
                token_budget=min(
                    QUESTION_REVIEW_CONTEXT_POLICY.max_evidence_item_tokens,
                    remaining,
                ),
                estimator=estimator,
                model=model,
            )
            bounded["content"] = content
        cost = estimator.estimate_text(
            json.dumps(bounded, ensure_ascii=False, sort_keys=True, default=str),
            model=model,
        )
        if cost > remaining:
            break
        selected_references.append(bounded)
        remaining -= cost
    return messages, selected_references


def _align_review_binding_with_scoring_input(
    retrieval: ReviewerReferenceResolution,
    scoring_references: list[dict],
) -> ReviewerReferenceResolution:
    binding = retrieval.review_binding
    if binding is None:
        return retrieval
    final_ids = tuple(
        dict.fromkeys(
            evidence_id
            for reference in scoring_references
            if (evidence_id := _reference_id(reference))
        )
    )
    replayed = tuple(
        evidence_id
        for evidence_id in binding.replayed_evidence_ids
        if evidence_id in final_ids
    )
    supplemental = tuple(
        evidence_id
        for evidence_id in binding.supplemental_evidence_ids
        if evidence_id in final_ids
    )
    aligned = ReviewEvidenceBinding.model_validate(
        {
            **binding.model_dump(),
            "replayed_evidence_ids": replayed,
            "supplemental_evidence_ids": supplemental,
            "supplemental_evidence_refs": tuple(
                reference
                for reference in binding.supplemental_evidence_refs
                if reference.evidence_id in supplemental
            ),
            "final_evidence_ids": tuple(dict.fromkeys((*replayed, *supplemental))),
        }
    )
    return ReviewerReferenceResolution(
        references=retrieval.references,
        retrieval_path=retrieval.retrieval_path,
        degraded_reason=retrieval.degraded_reason,
        replayed_evidence_ids=list(replayed),
        supplemental_evidence_ids=list(supplemental),
        decision=retrieval.decision,
        evidence_binding_id=aligned.binding_id,
        review_binding=aligned,
    )


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
