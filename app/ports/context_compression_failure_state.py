from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.context_compression_failure_containment import (
        AttemptAbortResult,
        AttemptAuthorization,
        AttemptFinishResult,
        FailureOutcome,
        FailureScope,
        FailureStateDecision,
        FailureStateRecord,
        ProviderCircuitScope,
        ValidationQuarantineScope,
    )


class ContextCompressionFailureStateStore(Protocol):
    """Persistence port for fenced compression failure-state transitions."""

    def get(self, state_key_sha256: str) -> FailureStateRecord | None: ...

    def before_attempt(
        self,
        *,
        scope: FailureScope,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision: ...

    def authorize_attempt(
        self,
        *,
        provider_scope: ProviderCircuitScope,
        validation_scope: ValidationQuarantineScope,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptAuthorization: ...

    def record_failure(
        self,
        *,
        scope: FailureScope,
        failure_code: str,
        decision: FailureStateDecision,
        threshold: int,
        cooldown_seconds: int,
        now: datetime,
    ) -> FailureStateRecord: ...

    def record_success(
        self,
        *,
        scope: FailureScope,
        decision: FailureStateDecision,
        now: datetime,
    ) -> FailureStateRecord: ...

    def heartbeat_probe(
        self,
        *,
        scope: FailureScope,
        decision: FailureStateDecision,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision: ...

    def heartbeat_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptAuthorization | bool: ...

    def finish_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        outcome: FailureOutcome,
        failure_code: str | None,
        provider_threshold: int,
        provider_cooldown_seconds: int,
        validation_threshold: int,
        validation_cooldown_seconds: int,
        now: datetime,
    ) -> AttemptFinishResult: ...

    def abort_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        reason: str,
        now: datetime,
    ) -> AttemptAbortResult: ...

    def delete_owner(
        self,
        *,
        owner_type: str,
        owner_key: str,
        privacy_scope_sha256: str | None = None,
    ) -> int: ...

    def cleanup_expired(
        self,
        *,
        before: datetime,
        now: datetime,
        batch_size: int,
    ) -> int: ...


__all__ = ["ContextCompressionFailureStateStore"]
