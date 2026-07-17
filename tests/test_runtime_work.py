from app.services.report import (
    ReportGenerationTimeout,
    ReportOutputFormatError,
)
from app.services.runtime_work import (
    RuntimeFailure,
    classify_runtime_failure,
    retry_delay_seconds,
)


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
