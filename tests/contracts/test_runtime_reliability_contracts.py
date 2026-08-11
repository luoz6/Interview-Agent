from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.runtime.reliability import (
    ErrorRule,
    ErrorTaxonomy,
    FencedMutation,
    IdempotencyReceipt,
    LeaseLost,
    LeaseToken,
    RetryPolicy,
    RetryableFailure,
    RuntimeFailure,
    TerminalFailure,
)
from app.adapters.reliability.runtime_failure import (
    RuntimeFailure as AdapterFailure,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def make_lease(
    *,
    token: str = "secret-token",
    fencing_version: int = 3,
    expires_at: datetime | None = None,
) -> LeaseToken:
    return LeaseToken(
        resource_id="report-job-1",
        owner_id="worker-1",
        token=token,
        fencing_version=fencing_version,
        expires_at=expires_at or NOW + timedelta(minutes=5),
    )


def test_lease_token_is_strict_and_does_not_expose_secret_in_repr():
    lease = make_lease()

    assert "secret-token" not in repr(lease)
    with pytest.raises(ValueError, match="fencing_version"):
        make_lease(fencing_version=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        make_lease(expires_at=NOW.replace(tzinfo=None))


def test_fenced_mutation_rejects_stale_owner_token_version_and_expiry():
    mutation = FencedMutation(
        resource_id="report-job-1",
        operation="complete",
        lease=make_lease(),
        idempotency_key="completion-1",
    )

    mutation.authorize(make_lease(), now=NOW)
    for current in (
        make_lease(token="other-token"),
        make_lease(fencing_version=4),
        make_lease(expires_at=NOW - timedelta(seconds=1)),
    ):
        with pytest.raises(LeaseLost):
            mutation.authorize(current, now=NOW)


def test_fenced_mutation_requires_resource_binding():
    with pytest.raises(ValueError, match="resource_id"):
        FencedMutation(
            resource_id="other-job",
            operation="complete",
            lease=make_lease(),
            idempotency_key="completion-1",
        )


def test_retry_policy_is_bounded_and_terminal_failures_never_retry():
    policy = RetryPolicy(delays_seconds=(1, 5, 30), max_attempts=3)

    assert [policy.delay_seconds(value) for value in range(1, 5)] == [
        1,
        5,
        30,
        30,
    ]
    assert policy.should_retry(
        RuntimeFailure("temporary", True),
        attempt_count=2,
    )
    assert not policy.should_retry(
        RuntimeFailure("temporary", True),
        attempt_count=3,
    )
    assert not policy.should_retry(
        RuntimeFailure("permanent", False),
        attempt_count=1,
    )


def test_error_taxonomy_uses_declared_order_and_stable_fallback():
    class ParentFailure(Exception):
        pass

    class ChildFailure(ParentFailure):
        pass

    taxonomy = ErrorTaxonomy(
        (
            ErrorRule((ChildFailure,), RuntimeFailure("child", False)),
            ErrorRule((ParentFailure,), RuntimeFailure("parent", True)),
        )
    )

    assert taxonomy.classify(ChildFailure()) == RuntimeFailure("child", False)
    assert taxonomy.classify(ParentFailure()) == RuntimeFailure("parent", True)
    assert taxonomy.classify(RuntimeError()) == RuntimeFailure(
        "unexpected_error",
        True,
    )


def test_idempotency_receipt_binds_operation_key_resource_and_fence():
    mutation = FencedMutation(
        resource_id="report-job-1",
        operation="complete",
        lease=make_lease(),
        idempotency_key="completion-1",
    )
    receipt = IdempotencyReceipt(
        resource_id="report-job-1",
        operation="complete",
        idempotency_key="completion-1",
        outcome_sha256="a" * 64,
        fencing_version=3,
        completed_at=NOW,
    )

    assert receipt.matches(mutation)
    assert "completion-1" not in repr(receipt)
    with pytest.raises(ValueError, match="SHA-256"):
        IdempotencyReceipt(
            resource_id="report-job-1",
            operation="complete",
            idempotency_key="completion-1",
            outcome_sha256="invalid",
            fencing_version=3,
            completed_at=NOW,
        )


def test_failure_contracts_expose_retry_semantics_without_message_parsing():
    assert LeaseLost.code == "lease_lost"
    assert LeaseLost.retryable is True
    assert RetryableFailure.retryable is True
    assert TerminalFailure.retryable is False


def test_compatibility_module_reexports_authoritative_runtime_failure():
    assert AdapterFailure is RuntimeFailure


def test_reliability_core_does_not_import_business_services():
    source_path = (
        Path(__file__).parents[2]
        / "app"
        / "runtime"
        / "reliability.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(
        module.startswith("app.services") for module in imported_modules
    )
