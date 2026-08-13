from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalRequest,
    RetrievalResult,
)
from app.domain.knowledge.rollout import (
    KnowledgeEngine,
    KnowledgeEngineAssignment,
    resolve_knowledge_engine_assignment,
)
from app.domain.knowledge.shadow import (
    RetrievalShadowComparison,
    RetrievalShadowFailure,
)
from app.ports.knowledge import RetrievalTraceSink
from app.application.knowledge.shadow_service import RetrievalShadowService


ShadowObservation = RetrievalShadowComparison | RetrievalShadowFailure


@dataclass(frozen=True)
class RuntimeRetrievalOutcome:
    result: RetrievalResult
    assignment: KnowledgeEngineAssignment
    shadow_observation: ShadowObservation | None = None
    runtime_reason_code: str | None = None


class RuntimeKnowledgeRetrievalService:
    """Owns engine assignment and compare-only Shadow orchestration."""

    def __init__(
        self,
        legacy_engine,
        candidate_engine,
        *,
        rollout_percent: int,
        assignment_version: str,
        shadow_enabled: bool = False,
        trace_sink: RetrievalTraceSink | None = None,
    ) -> None:
        self._legacy = legacy_engine
        self._candidate = candidate_engine
        self._rollout_percent = rollout_percent
        self._assignment_version = assignment_version
        self._shadow_enabled = shadow_enabled
        self._trace_sink = trace_sink
        self._shadow = RetrievalShadowService(legacy_engine, candidate_engine)

    def close(self) -> None:
        close = getattr(self._candidate, "close", None)
        if callable(close):
            close()

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        legacy_profile: ResolvedRetrievalProfile,
        candidate_profile: ResolvedRetrievalProfile,
        existing_assignment: KnowledgeEngineAssignment | None = None,
    ) -> RuntimeRetrievalOutcome:
        assignment_key = request.prep_run_id or request.session_id
        if not assignment_key:
            raise ValueError("runtime retrieval requires a stable session_id or prep_run_id")
        assignment = resolve_knowledge_engine_assignment(
            assignment_key,
            rollout_percent=(0 if self._shadow_enabled else self._rollout_percent),
            assignment_version=self._assignment_version,
            existing=existing_assignment,
        )

        if self._shadow_enabled:
            formal_result = self._legacy.retrieve(request, legacy_profile)
            _, observation = self._shadow.compare_with_legacy(
                request,
                legacy_result=formal_result,
                candidate_profile=candidate_profile,
            )
            outcome = RuntimeRetrievalOutcome(
                result=formal_result,
                assignment=assignment,
                shadow_observation=observation,
            )
            self._record(outcome, request)
            return outcome

        if assignment.engine == KnowledgeEngine.HYBRID_V2:
            try:
                result = self._candidate.retrieve(request, candidate_profile)
                outcome = RuntimeRetrievalOutcome(result=result, assignment=assignment)
            except Exception:
                outcome = RuntimeRetrievalOutcome(
                    result=self._legacy.retrieve(request, legacy_profile),
                    assignment=assignment,
                    runtime_reason_code="candidate_engine_failed",
                )
        else:
            outcome = RuntimeRetrievalOutcome(
                result=self._legacy.retrieve(request, legacy_profile),
                assignment=assignment,
            )
        self._record(outcome, request)
        return outcome

    def _record(
        self,
        outcome: RuntimeRetrievalOutcome,
        request: RetrievalRequest,
    ) -> None:
        if self._trace_sink is None:
            return
        payload: dict[str, Any] = {
            "trace_scope_id": request.prep_run_id or request.request_id,
            "request_id": request.request_id,
            "intent": request.intent.value,
            "assignment": outcome.assignment.model_dump(mode="json"),
            "formal_engine_version": outcome.result.retrieval_engine_version,
            "formal_profile_version": outcome.result.profile_version,
            "runtime_reason_code": outcome.runtime_reason_code,
            "retrieval_trace": outcome.result.trace.model_dump(mode="json"),
        }
        if outcome.shadow_observation is not None:
            payload["shadow"] = outcome.shadow_observation.model_dump(mode="json")
        try:
            self._trace_sink.record_retrieval_trace(payload)
        except Exception:
            # Observability is compare-only and must never change business facts.
            return
