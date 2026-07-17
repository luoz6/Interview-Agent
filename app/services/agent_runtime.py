from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Generic, Iterable, Iterator, Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.report import utc_now_iso


AgentName = Literal[
    "orchestrator",
    "knowledge",
    "examiner",
    "shadow_reviewer",
    "report_coach",
]
AgentPhase = Literal["prep", "interview", "review"]
AgentRunStatus = Literal["completed", "degraded", "failed", "cancelled"]
T = TypeVar("T")


class AgentExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent-runtime-v1"] = "agent-runtime-v1"
    run_id: str = Field(default_factory=lambda: f"agent-{uuid4().hex}")
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    agent: AgentName
    operation: str = Field(min_length=1)
    phase: AgentPhase
    session_id: str | None = None
    question_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    command_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    attempt_number: int = Field(default=1, ge=1)

    @field_validator("evidence_ids")
    @classmethod
    def deduplicate_evidence_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in value if item))


class AgentRunRecord(AgentExecutionContext):
    status: AgentRunStatus
    started_at: str
    finished_at: str
    latency_ms: float = Field(ge=0)
    fallback_reason: str | None = None
    error_code: str | None = None
    output_type: str | None = None
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


def correlation_id_from_plan(plan, *, session_id: str | None = None) -> str:
    prep_context = getattr(plan, "prep_context", None)
    snapshot = getattr(prep_context, "binding_snapshot", None)
    prep_run_id = getattr(snapshot, "prep_run_id", None)
    if isinstance(prep_run_id, str) and prep_run_id:
        return prep_run_id
    if isinstance(session_id, str) and session_id:
        return session_id
    return f"prep-{uuid4().hex}"


def evidence_ids_for_question(plan, question_id: str | None) -> list[str]:
    if not question_id:
        return []
    prep_context = getattr(plan, "prep_context", None)
    hints = getattr(prep_context, "question_hints", []) if prep_context else []
    for hint in hints:
        if hint.question_id == question_id:
            return list(dict.fromkeys(hint.evidence_ids))
    return []


class AgentRunRecorder(Protocol):
    def record(self, record: AgentRunRecord):
        pass


@dataclass(frozen=True)
class AgentFallback(Generic[T]):
    output: T
    reason: str


@dataclass(frozen=True)
class AgentOutcome:
    status: Literal["completed", "degraded"] = "completed"
    reason: str | None = None


class AgentExecutionRunner:
    def __init__(self, *, recorder: AgentRunRecorder | None = None) -> None:
        if recorder is None:
            from app.services.agent_trace import AgentTraceRecorder

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
                safe_metadata=metadata(resolved.output) if metadata else {},
            )
            return resolved.output
        outcome = classify(output) if classify else AgentOutcome()
        self._emit(
            context,
            status=outcome.status,
            started_at=started_at,
            started=started,
            fallback_reason=outcome.reason,
            output=output,
            safe_metadata=metadata(output) if metadata else {},
        )
        return output

    def stream(
        self,
        context: AgentExecutionContext,
        invoke: Callable[[], Iterable[T]],
        *,
        fallback: Callable[[Exception], AgentFallback[Iterable[T]]] | None = None,
    ) -> Iterator[T]:
        started_at = utc_now_iso()
        started = perf_counter()
        emitted = 0
        status: AgentRunStatus = "completed"
        fallback_reason = None
        error_code = None
        try:
            try:
                for item in invoke():
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
                safe_metadata={"emitted_chunks": emitted},
            )

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
            safe_metadata=safe_metadata or {},
        )
        try:
            self._recorder.record(record)
        except Exception:
            return
