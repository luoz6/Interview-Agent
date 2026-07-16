from dataclasses import dataclass
from typing import Literal

from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)


OutboxStatus = Literal[
    "pending",
    "running",
    "retrying",
    "published",
    "dead_letter",
]
ReceiptStatus = Literal[
    "running",
    "retrying",
    "completed",
    "dead_letter",
]
RETRY_DELAYS_SECONDS = (1, 5, 30, 120)


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    retryable: bool


def retry_delay_seconds(attempt_count: int) -> int:
    index = min(
        max(attempt_count, 1) - 1,
        len(RETRY_DELAYS_SECONDS) - 1,
    )
    return RETRY_DELAYS_SECONDS[index]


def classify_runtime_failure(exc: Exception) -> RuntimeFailure:
    if isinstance(exc, ReportGenerationTimeout):
        return RuntimeFailure("provider_timeout", True)
    if isinstance(exc, ReportOutputFormatError):
        return RuntimeFailure("invalid_provider_output", False)
    if isinstance(exc, ReportGenerationFailed):
        return RuntimeFailure("provider_unavailable", True)
    if exc.__class__.__module__.startswith("psycopg2"):
        return RuntimeFailure("database_unavailable", True)
    if isinstance(exc, (ValueError, TypeError)):
        return RuntimeFailure("domain_validation_failed", False)
    return RuntimeFailure("unexpected_error", True)
