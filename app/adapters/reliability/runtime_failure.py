from __future__ import annotations

from typing import Literal

from app.runtime.reliability import (
    DEFAULT_RETRY_POLICY,
    ErrorRule,
    ErrorTaxonomy,
    RuntimeFailure,
)
from app.domain.context.artifacts import (
    ContextArtifactBusy,
    ContextArtifactConflict,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
)
from app.services.context_budget import ContextBudgetExceeded
from app.services.model_capabilities import ContextConfigurationError
from app.services.postgres_connections import PostgresPoolExhausted
from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)
from app.services.token_estimation import ContextEstimatorUnavailable
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
RETRY_DELAYS_SECONDS = DEFAULT_RETRY_POLICY.delays_seconds


RUNTIME_ERROR_TAXONOMY = ErrorTaxonomy(
    (
        ErrorRule((ContextArtifactBusy,), RuntimeFailure("context_artifact_busy", True)),
        ErrorRule(
            (ContextArtifactLeaseLost,),
            RuntimeFailure("context_artifact_lease_lost", True),
        ),
        ErrorRule(
            (ContextArtifactConflict,),
            RuntimeFailure("context_artifact_conflict", False),
        ),
        ErrorRule(
            (ContextArtifactMissing,),
            RuntimeFailure("context_artifact_missing", False),
        ),
        ErrorRule(
            (ContextArtifactValidationFailed,),
            RuntimeFailure("context_artifact_validation_failed", False),
        ),
        ErrorRule(
            (ContextArtifactProviderFailed,),
            RuntimeFailure("context_artifact_provider_failed", True),
        ),
        ErrorRule(
            (ContextBudgetExceeded,),
            RuntimeFailure("context_budget_exceeded", False),
        ),
        ErrorRule(
            (ContextConfigurationError,),
            RuntimeFailure("context_configuration_error", False),
        ),
        ErrorRule(
            (ContextEstimatorUnavailable,),
            RuntimeFailure("context_estimator_unavailable", False),
        ),
        ErrorRule(
            (PostgresPoolExhausted,),
            RuntimeFailure("postgres_pool_exhausted", True),
        ),
        ErrorRule((WorkflowThreadBusy,), RuntimeFailure("workflow_thread_busy", True)),
        ErrorRule(
            (WorkflowThreadLockLost,),
            RuntimeFailure("workflow_thread_lock_lost", True),
        ),
        ErrorRule(
            (GenerationLeaseLost,),
            RuntimeFailure("generation_lease_lost", True),
        ),
        ErrorRule((ReportLeaseLost,), RuntimeFailure("report_lease_lost", True)),
        ErrorRule(
            (FencedWriteRejected,),
            RuntimeFailure("fenced_write_rejected", False),
        ),
        ErrorRule(
            (ProjectionConflict,),
            RuntimeFailure("projection_conflict", False),
        ),
        ErrorRule(
            (ReportCommitConflict,),
            RuntimeFailure("report_commit_conflict", False),
        ),
        ErrorRule((ReviewEffectBusy,), RuntimeFailure("review_effect_busy", True)),
        ErrorRule(
            (ReviewEffectConflict,),
            RuntimeFailure("review_effect_conflict", False),
        ),
        ErrorRule(
            (ReportGenerationTimeout, TimeoutError),
            RuntimeFailure("provider_timeout", True),
        ),
        ErrorRule(
            (ReportOutputFormatError,),
            RuntimeFailure("invalid_provider_output", False),
        ),
        ErrorRule(
            (ReportGenerationFailed, ConnectionError),
            RuntimeFailure("provider_unavailable", True),
        ),
        ErrorRule((PermissionError,), RuntimeFailure("provider_auth_failed", False)),
        ErrorRule(
            (AssertionError, KeyError, ValueError, TypeError),
            RuntimeFailure("domain_validation_failed", False),
        ),
    )
)


def retry_delay_seconds(attempt_count: int) -> int:
    return DEFAULT_RETRY_POLICY.delay_seconds(attempt_count)


def classify_runtime_failure(exc: Exception) -> RuntimeFailure:
    if isinstance(exc, ContextArtifactProviderFailed) and isinstance(
        exc.__cause__, Exception
    ):
        cause_failure = classify_runtime_failure(exc.__cause__)
        if cause_failure.code != "unexpected_error":
            return cause_failure
    if exc.__class__.__module__.startswith("psycopg2"):
        return RuntimeFailure("database_unavailable", True)
    return RUNTIME_ERROR_TAXONOMY.classify(exc)


__all__ = [
    "OutboxStatus",
    "RETRY_DELAYS_SECONDS",
    "ReceiptStatus",
    "RuntimeFailure",
    "classify_runtime_failure",
    "retry_delay_seconds",
]
