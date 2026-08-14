from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalRequest,
    RetrievalResult,
)
from app.domain.knowledge.engine import (
    KnowledgeEngine,
    RuntimeEngineExecution,
    RuntimeFallbackReason,
)
from app.ports.knowledge import RetrievalTraceSink


@dataclass(frozen=True)
class RuntimeRetrievalOutcome:
    result: RetrievalResult
    execution: RuntimeEngineExecution


class RuntimeKnowledgeRetrievalService:
    """Runs the explicitly configured engine with a narrow Legacy fallback."""

    def __init__(
        self,
        legacy_engine,
        candidate_engine,
        *,
        configured_engine: KnowledgeEngine | str,
        trace_sink: RetrievalTraceSink | None = None,
    ) -> None:
        self._legacy = legacy_engine
        self._candidate = candidate_engine
        self._configured_engine = KnowledgeEngine(configured_engine)
        self._trace_sink = trace_sink

    def close(self) -> None:
        close = getattr(self._candidate, "close", None)
        if callable(close):
            close()

    def inspect(
        self,
        request: RetrievalRequest,
        *,
        profile: ResolvedRetrievalProfile,
        engine: str,
    ) -> RetrievalResult:
        """Run a console diagnostic without assignment, Shadow, or trace recording."""

        if engine == "legacy":
            return self._legacy.retrieve(request, profile)
        if engine == "hybrid-v2":
            return self._candidate.retrieve(request, profile)
        raise ValueError("unsupported diagnostic engine")

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        legacy_profile: ResolvedRetrievalProfile,
        candidate_profile: ResolvedRetrievalProfile,
    ) -> RuntimeRetrievalOutcome:
        if self._configured_engine == KnowledgeEngine.HYBRID_V2:
            try:
                result = self._candidate.retrieve(request, candidate_profile)
            except Exception:
                outcome = self._legacy_fallback(
                    request,
                    legacy_profile=legacy_profile,
                    reason=RuntimeFallbackReason.CANDIDATE_ENGINE_FAILED,
                )
            else:
                if result.availability == RetrievalAvailability.UNAVAILABLE:
                    outcome = self._legacy_fallback(
                        request,
                        legacy_profile=legacy_profile,
                        reason=RuntimeFallbackReason.RETRIEVAL_UNAVAILABLE,
                    )
                else:
                    outcome = RuntimeRetrievalOutcome(
                        result=result,
                        execution=self._execution(
                            requested=KnowledgeEngine.HYBRID_V2,
                            effective=KnowledgeEngine.HYBRID_V2,
                            result=result,
                        ),
                    )
        else:
            result = self._legacy.retrieve(request, legacy_profile)
            outcome = RuntimeRetrievalOutcome(
                result=result,
                execution=self._execution(
                    requested=KnowledgeEngine.LEGACY,
                    effective=KnowledgeEngine.LEGACY,
                    result=result,
                ),
            )
        self._record(outcome, request)
        return outcome

    def _legacy_fallback(
        self,
        request: RetrievalRequest,
        *,
        legacy_profile: ResolvedRetrievalProfile,
        reason: RuntimeFallbackReason,
    ) -> RuntimeRetrievalOutcome:
        """Run the narrow fallback once and let a Legacy failure propagate."""

        fallback = self._legacy.retrieve(request, legacy_profile)
        return RuntimeRetrievalOutcome(
            result=fallback,
            execution=self._execution(
                requested=KnowledgeEngine.HYBRID_V2,
                effective=KnowledgeEngine.LEGACY,
                result=fallback,
                fallback_reason=reason,
            ),
        )

    @staticmethod
    def _execution(
        *,
        requested: KnowledgeEngine,
        effective: KnowledgeEngine,
        result: RetrievalResult,
        fallback_reason: RuntimeFallbackReason | None = None,
    ) -> RuntimeEngineExecution:
        return RuntimeEngineExecution(
            requested_engine=requested,
            effective_engine=effective,
            fallback_reason=fallback_reason,
            retrieval_availability=result.availability.value,
            engine_version=result.retrieval_engine_version,
        )

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
            "execution": outcome.execution.model_dump(mode="json"),
            "formal_engine_version": outcome.result.retrieval_engine_version,
            "formal_profile_version": outcome.result.profile_version,
            "fallback_reason": (
                outcome.execution.fallback_reason.value
                if outcome.execution.fallback_reason is not None
                else None
            ),
            "retrieval_trace": outcome.result.trace.model_dump(mode="json"),
        }
        try:
            self._trace_sink.record_retrieval_trace(payload)
        except Exception:
            # Observability is compare-only and must never change business facts.
            return
