from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Callable


class GenerationAlreadyCompleted(RuntimeError):
    pass


class GenerationLeaseConflict(RuntimeError):
    pass


class GenerationInputConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class InterviewGeneration:
    generation_id: str
    session_id: str
    source_command_id: str
    question_id: str
    status: str
    active_attempt: int
    final_text: str | None
    source_decision_id: str | None = None
    decision_prompt_version: str | None = None
    decision_prompt_sha256: str | None = None
    generation_prompt_version: str | None = None
    generation_prompt_sha256: str | None = None
    generation_kind: str = "followup"
    identity_sha256: str | None = None
    intent_sha256: str | None = None
    context_sha256: str | None = None
    knowledge_scope_sha256: str | None = None
    generator_version: str | None = None
    result_mode: str | None = None
    failure_reason_code: str | None = None
    provider_invocation_count: int | None = None
    generation_latency_ms: int | None = None
    fallback_used: bool | None = None
    safe_reason_code: str | None = None


@dataclass(frozen=True)
class GenerationAttempt:
    generation_id: str
    attempt_number: int
    status: str
    lease_owner: str | None
    lease_token: str
    fencing_version: int
    lease_expires_at: datetime
    reclaimed_after_expiry: bool = False


@dataclass(frozen=True)
class GenerationEvent:
    generation_id: str
    attempt_number: int
    sequence: int
    event_type: str
    delta: str


class ChunkCoalescer:
    def __init__(
        self,
        *,
        max_interval_seconds: float = 0.2,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.max_interval_seconds = max_interval_seconds
        self._clock = clock
        self._parts: list[str] = []
        self._started = clock()

    def add(self, value: str) -> str | None:
        self._parts.append(value)
        if self._clock() - self._started < self.max_interval_seconds:
            return None
        return self.flush()

    def flush(self) -> str | None:
        if not self._parts:
            return None
        value = "".join(self._parts)
        self._parts.clear()
        self._started = self._clock()
        return value
