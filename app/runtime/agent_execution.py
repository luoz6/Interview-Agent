from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Callable, Iterable, Iterator, Protocol, TypeVar

from app.domain.agent_execution import (
    AgentExecutionContext,
    AgentFallback,
    AgentName,
    AgentOutcome,
    AgentPhase,
    AgentRunRecord,
    AgentRunStatus,
    correlation_id_from_plan,
    evidence_ids_for_question,
)
from app.domain.report.models import utc_now_iso
from app.domain.trace_sanitization import sanitize_agent_safe_metadata
from app.runtime.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
)


T = TypeVar("T")
logger = logging.getLogger(__name__)


class AgentRunRecorder(Protocol):
    def record(self, record: AgentRunRecord):
        pass


class AgentExecutionRunner:
    def __init__(self, *, recorder: AgentRunRecorder | None = None) -> None:
        if recorder is None:
            from app.adapters.observability.agent_trace import AgentTraceRecorder

            recorder = AgentTraceRecorder.from_env()
        self._recorder = recorder

    def run(
        self,
        context: AgentExecutionContext,
        invoke: Callable[[], T],
        *,
        fallback: Callable[[Exception], AgentFallback[T]] | None = None,
        metadata: Callable[[T], dict[str, Any]] | None = None,
        classify: Callable[[T], AgentOutcome] | None = None,
    ) -> T:
        reset_provider_context_metadata()
        context = context.model_copy(deep=True)
        started_at = utc_now_iso()
        started = perf_counter()
        try:
            output = invoke()
        except Exception as exc:
            if fallback is None:
                self._emit(
                    context,
                    status="failed",
                    started_at=started_at,
                    started=started,
                    error_code=type(exc).__name__,
                )
                raise
            try:
                resolved = fallback(exc)
            except Exception as fallback_exc:
                self._emit(
                    context,
                    status="failed",
                    started_at=started_at,
                    started=started,
                    error_code=type(fallback_exc).__name__,
                )
                raise
            self._emit(
                context,
                status="degraded",
                started_at=started_at,
                started=started,
                fallback_reason=resolved.reason,
                output=resolved.output,
                safe_metadata=self._resolve_metadata(
                    context,
                    metadata,
                    resolved.output,
                ),
            )
            return resolved.output
        outcome = self._resolve_outcome(context, classify, output)
        self._emit(
            context,
            status=outcome.status,
            started_at=started_at,
            started=started,
            fallback_reason=outcome.reason,
            output=output,
            safe_metadata=self._resolve_metadata(context, metadata, output),
        )
        return output

    def stream(
        self,
        context: AgentExecutionContext,
        invoke: Callable[[], Iterable[T]],
        *,
        fallback: Callable[[Exception], AgentFallback[Iterable[T]]] | None = None,
    ) -> Iterator[T]:
        context_snapshot = context.model_copy(deep=True)
        return self._stream(
            context_snapshot,
            invoke,
            fallback=fallback,
        )

    def _stream(
        self,
        context: AgentExecutionContext,
        invoke: Callable[[], Iterable[T]],
        *,
        fallback: Callable[[Exception], AgentFallback[Iterable[T]]] | None,
    ) -> Iterator[T]:
        reset_provider_context_metadata()
        started_at = utc_now_iso()
        started = perf_counter()
        emitted = 0
        first_item_latency_ms: float | None = None
        status: AgentRunStatus = "completed"
        fallback_reason = None
        error_code = None
        try:
            try:
                for item in invoke():
                    if first_item_latency_ms is None:
                        first_item_latency_ms = round(
                            (perf_counter() - started) * 1000,
                            3,
                        )
                    emitted += 1
                    yield item
            except Exception as exc:
                if fallback is None:
                    status = "failed"
                    error_code = type(exc).__name__
                    raise
                resolved = fallback(exc)
                status = "degraded"
                fallback_reason = resolved.reason
                for item in resolved.output:
                    emitted += 1
                    yield item
        except GeneratorExit:
            status = "cancelled"
            fallback_reason = "client_disconnected"
            raise
        except Exception as exc:
            status = "failed"
            error_code = type(exc).__name__
            raise
        finally:
            self._emit(
                context,
                status=status,
                started_at=started_at,
                started=started,
                fallback_reason=fallback_reason,
                error_code=error_code,
                output_type="stream",
                safe_metadata={
                    "emitted_chunks": emitted,
                    **(
                        {"first_item_latency_ms": first_item_latency_ms}
                        if first_item_latency_ms is not None
                        else {}
                    ),
                },
            )

    def _resolve_outcome(
        self,
        context: AgentExecutionContext,
        classify: Callable[[T], AgentOutcome] | None,
        output: T,
    ) -> AgentOutcome:
        if classify is None:
            return AgentOutcome()
        try:
            outcome = classify(output)
            if not isinstance(outcome, AgentOutcome):
                raise TypeError("agent outcome classifier returned an invalid result")
            return outcome
        except Exception:
            self._warn(
                context,
                message="agent telemetry helper failed",
                error_code="agent_outcome_classification_failed",
            )
            return AgentOutcome()

    def _resolve_metadata(
        self,
        context: AgentExecutionContext,
        metadata: Callable[[T], dict[str, Any]] | None,
        output: T,
    ) -> dict[str, Any]:
        if metadata is None:
            return {}
        try:
            return metadata(output)
        except Exception:
            self._warn(
                context,
                message="agent telemetry helper failed",
                error_code="agent_metadata_extraction_failed",
            )
            return {}

    def _emit(
        self,
        context: AgentExecutionContext,
        *,
        status: AgentRunStatus,
        started_at: str,
        started: float,
        fallback_reason: str | None = None,
        error_code: str | None = None,
        output: Any = None,
        output_type: str | None = None,
        safe_metadata: dict[str, Any] | None = None,
    ) -> None:
        provider_metadata = consume_provider_context_metadata()
        sanitized = sanitize_agent_safe_metadata(
            {**provider_metadata, **(safe_metadata or {})}
        )
        if sanitized.rejected_count:
            self._warn(
                context,
                message="agent metadata sanitized",
                error_code="agent_metadata_sanitized",
                rejected_count=sanitized.rejected_count,
            )
        record = AgentRunRecord(
            **context.model_dump(),
            status=status,
            started_at=started_at,
            finished_at=utc_now_iso(),
            latency_ms=round((perf_counter() - started) * 1000, 3),
            fallback_reason=fallback_reason,
            error_code=error_code,
            output_type=output_type
            or (type(output).__name__ if output is not None else None),
            safe_metadata=sanitized.value,
        )
        try:
            self._recorder.record(record)
        except Exception:
            self._warn(
                context,
                message="agent run emission failed",
                error_code="agent_run_emission_failed",
            )

    @staticmethod
    def _warn(
        context: AgentExecutionContext,
        *,
        message: str,
        error_code: str,
        rejected_count: int | None = None,
    ) -> None:
        extra = {
            "run_id": context.run_id,
            "agent": context.agent,
            "operation": context.operation,
            "error_code": error_code,
        }
        if rejected_count is not None:
            extra["rejected_count"] = rejected_count
        logger.warning(message, extra=extra)
