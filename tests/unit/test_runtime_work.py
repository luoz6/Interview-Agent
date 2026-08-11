"""Unit tests for runtime work error taxonomy and retry policy."""

import pytest

from app.services.report import (
    ReportGenerationTimeout,
    ReportOutputFormatError,
)
from app.adapters.reliability.runtime_failure import (
    RuntimeFailure,
    classify_runtime_failure,
    retry_delay_seconds,
)
from app.services.context_budget import ContextBudgetExceeded
from app.domain.context.artifacts import (
    ContextArtifactBusy,
    ContextArtifactConflict,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
)
from app.services.model_capabilities import ContextConfigurationError
from app.services.token_estimation import ContextEstimatorUnavailable
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    GenerationLeaseLost,
    ProjectionConflict,
    ReportCommitConflict,
    ReportLeaseLost,
    ReviewEffectLeaseLost,
    WorkflowThreadBusy,
    WorkflowThreadLockLost,
)


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (WorkflowThreadBusy(), "workflow_thread_busy", True),
        (WorkflowThreadLockLost(), "workflow_thread_lock_lost", True),
        (GenerationLeaseLost(), "generation_lease_lost", True),
        (ReportLeaseLost(), "report_lease_lost", True),
        (FencedWriteRejected(), "fenced_write_rejected", False),
        (ReviewEffectLeaseLost(), "fenced_write_rejected", False),
        (ProjectionConflict(), "projection_conflict", False),
        (ReportCommitConflict(), "report_commit_conflict", False),
    ],
)
def test_classify_workflow_ownership_failures(exc, code, retryable):
    assert classify_runtime_failure(exc) == RuntimeFailure(code, retryable)


def test_retry_schedule_is_bounded():
    assert [retry_delay_seconds(value) for value in range(1, 6)] == [
        1,
        5,
        30,
        120,
        120,
    ]


def test_provider_timeout_is_retryable_without_message():
    failure = classify_runtime_failure(ReportGenerationTimeout("secret"))

    assert failure == RuntimeFailure("provider_timeout", True)
    assert "secret" not in repr(failure)


def test_invalid_output_is_permanent():
    failure = classify_runtime_failure(ReportOutputFormatError("raw"))

    assert failure == RuntimeFailure("invalid_provider_output", False)


def test_unexpected_error_is_bounded_by_receipt():
    assert classify_runtime_failure(RuntimeError("x")) == RuntimeFailure(
        "unexpected_error",
        True,
    )


def test_builtin_provider_failures_share_the_runtime_classifier():
    assert classify_runtime_failure(TimeoutError()) == RuntimeFailure(
        "provider_timeout",
        True,
    )
    assert classify_runtime_failure(ConnectionError()) == RuntimeFailure(
        "provider_unavailable",
        True,
    )
    assert classify_runtime_failure(PermissionError()) == RuntimeFailure(
        "provider_auth_failed",
        False,
    )


def test_programming_and_domain_errors_are_not_retried():
    for error in (AssertionError(), KeyError(), TypeError(), ValueError()):
        assert classify_runtime_failure(error) == RuntimeFailure(
            "domain_validation_failed",
            False,
        )


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (
            ContextBudgetExceeded(
                operation="test",
                estimated_input_tokens=11,
                available_input_tokens=10,
            ),
            "context_budget_exceeded",
        ),
        (ContextConfigurationError("bad config"), "context_configuration_error"),
        (ContextEstimatorUnavailable("missing"), "context_estimator_unavailable"),
    ],
)
def test_context_failures_are_stable_and_non_retryable(exc, code):
    assert classify_runtime_failure(exc) == RuntimeFailure(code, False)


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (ContextArtifactBusy(), "context_artifact_busy", True),
        (ContextArtifactLeaseLost(), "context_artifact_lease_lost", True),
        (ContextArtifactConflict(), "context_artifact_conflict", False),
        (ContextArtifactMissing(), "context_artifact_missing", False),
        (
            ContextArtifactValidationFailed(),
            "context_artifact_validation_failed",
            False,
        ),
        (
            ContextArtifactProviderFailed(),
            "context_artifact_provider_failed",
            True,
        ),
    ],
)
def test_context_artifact_failure_taxonomy(exc, code, retryable):
    assert classify_runtime_failure(exc) == RuntimeFailure(code, retryable)


def test_context_artifact_provider_failure_preserves_cause_classification():
    try:
        raise ContextArtifactProviderFailed() from TimeoutError()
    except ContextArtifactProviderFailed as exc:
        failure = classify_runtime_failure(exc)

    assert failure == RuntimeFailure("provider_timeout", True)
