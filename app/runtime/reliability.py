from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from app.domain.execution_lease import FencedMutation, LeaseLost, LeaseToken


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    retryable: bool

    def __post_init__(self) -> None:
        _require_non_empty("code", self.code)


class RetryableFailure(RuntimeError):
    code = "retryable_failure"
    retryable = True


class TerminalFailure(RuntimeError):
    code = "terminal_failure"
    retryable = False


@dataclass(frozen=True)
class RetryPolicy:
    delays_seconds: tuple[int, ...] = (1, 5, 30, 120)
    max_attempts: int = 4

    def __post_init__(self) -> None:
        if not self.delays_seconds:
            raise ValueError("delays_seconds must not be empty")
        if any(
            isinstance(delay, bool) or delay < 0
            for delay in self.delays_seconds
        ):
            raise ValueError("retry delays must be non-negative integers")
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")

    def delay_seconds(self, attempt_count: int) -> int:
        if isinstance(attempt_count, bool) or attempt_count < 1:
            raise ValueError("attempt_count must be a positive integer")
        index = min(attempt_count - 1, len(self.delays_seconds) - 1)
        return self.delays_seconds[index]

    def should_retry(
        self,
        failure: RuntimeFailure,
        *,
        attempt_count: int,
    ) -> bool:
        if isinstance(attempt_count, bool) or attempt_count < 1:
            raise ValueError("attempt_count must be a positive integer")
        return failure.retryable and attempt_count < self.max_attempts


@dataclass(frozen=True)
class ErrorRule:
    exception_types: tuple[type[Exception], ...]
    failure: RuntimeFailure

    def __post_init__(self) -> None:
        if not self.exception_types:
            raise ValueError("exception_types must not be empty")
        if any(not issubclass(item, Exception) for item in self.exception_types):
            raise TypeError("exception_types must contain Exception classes")


class ErrorTaxonomy:
    def __init__(
        self,
        rules: Iterable[ErrorRule],
        *,
        fallback: RuntimeFailure | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._fallback = fallback or RuntimeFailure("unexpected_error", True)

    def classify(self, exc: Exception) -> RuntimeFailure:
        for rule in self._rules:
            if isinstance(exc, rule.exception_types):
                return rule.failure
        return self._fallback


@dataclass(frozen=True)
class IdempotencyReceipt:
    resource_id: str
    operation: str
    idempotency_key: str = field(repr=False)
    outcome_sha256: str
    fencing_version: int
    completed_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("resource_id", self.resource_id)
        _require_non_empty("operation", self.operation)
        _require_non_empty("idempotency_key", self.idempotency_key)
        if not _SHA256_PATTERN.fullmatch(self.outcome_sha256):
            raise ValueError("outcome_sha256 must be lowercase SHA-256 hex")
        if isinstance(self.fencing_version, bool) or self.fencing_version < 1:
            raise ValueError("fencing_version must be a positive integer")
        _require_aware_datetime("completed_at", self.completed_at)

    def matches(self, mutation: FencedMutation) -> bool:
        return (
            self.resource_id == mutation.resource_id
            and self.operation == mutation.operation
            and self.idempotency_key == mutation.idempotency_key
            and self.fencing_version == mutation.lease.fencing_version
        )


DEFAULT_RETRY_POLICY = RetryPolicy()


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
