from dataclasses import dataclass
from typing import Literal

from app.services.postgres_connections import PostgresPoolExhausted
from app.services.context_budget import ContextBudgetExceeded
from app.services.model_capabilities import ContextConfigurationError
from app.services.token_estimation import ContextEstimatorUnavailable

from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    GenerationLeaseLost,
    ProjectionConflict,
    ReportCommitConflict,
    ReportLeaseLost,
    ReviewEffectBusy,
    ReviewEffectConflict,
    WorkflowThreadBusy,
    WorkflowThreadLockLost,
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
    if isinstance(exc, ContextBudgetExceeded):
        return RuntimeFailure("context_budget_exceeded", False)
    if isinstance(exc, ContextConfigurationError):
        return RuntimeFailure("context_configuration_error", False)
    if isinstance(exc, ContextEstimatorUnavailable):
        return RuntimeFailure("context_estimator_unavailable", False)
    if isinstance(exc, PostgresPoolExhausted):
        return RuntimeFailure("postgres_pool_exhausted", True)
    if isinstance(exc, WorkflowThreadBusy):
        return RuntimeFailure("workflow_thread_busy", True)
    if isinstance(exc, WorkflowThreadLockLost):
        return RuntimeFailure("workflow_thread_lock_lost", True)
    if isinstance(exc, GenerationLeaseLost):
        return RuntimeFailure("generation_lease_lost", True)
    if isinstance(exc, ReportLeaseLost):
        return RuntimeFailure("report_lease_lost", True)
    if isinstance(exc, FencedWriteRejected):
        return RuntimeFailure("fenced_write_rejected", False)
    if isinstance(exc, ProjectionConflict):
        return RuntimeFailure("projection_conflict", False)
    if isinstance(exc, ReportCommitConflict):
        return RuntimeFailure("report_commit_conflict", False)
    if isinstance(exc, ReviewEffectBusy):
        return RuntimeFailure("review_effect_busy", True)
    if isinstance(exc, ReviewEffectConflict):
        return RuntimeFailure("review_effect_conflict", False)
    if isinstance(exc, ReportGenerationTimeout):
        return RuntimeFailure("provider_timeout", True)
    if isinstance(exc, ReportOutputFormatError):
        return RuntimeFailure("invalid_provider_output", False)
    if isinstance(exc, ReportGenerationFailed):
        return RuntimeFailure("provider_unavailable", True)
    if isinstance(exc, TimeoutError):
        return RuntimeFailure("provider_timeout", True)
    if isinstance(exc, ConnectionError):
        return RuntimeFailure("provider_unavailable", True)
    if isinstance(exc, PermissionError):
        return RuntimeFailure("provider_auth_failed", False)
    if exc.__class__.__module__.startswith("psycopg2"):
        return RuntimeFailure("database_unavailable", True)
    if isinstance(exc, (AssertionError, KeyError, ValueError, TypeError)):
        return RuntimeFailure("domain_validation_failed", False)
    return RuntimeFailure("unexpected_error", True)
