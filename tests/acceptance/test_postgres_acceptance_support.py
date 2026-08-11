from __future__ import annotations

"""Acceptance coverage for shared PostgreSQL execution support."""

import base64
from datetime import datetime, timezone

import pytest

from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_postgres_scope_approval,
    load_receipt_signer,
)


def _approval_environment() -> dict[str, str]:
    return {
        "POSTGRES_ACCEPTANCE_APPROVAL_ID": "approval-2026-08-10",
        "POSTGRES_ACCEPTANCE_APPROVAL_RECEIPT_SHA256": "a" * 64,
        "POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT": "b" * 64,
        "POSTGRES_ACCEPTANCE_DATABASE_ALLOWLIST": "interview_test, audit_test ",
        "POSTGRES_ACCEPTANCE_APPROVAL_EXPIRES_AT": "2026-08-11T12:00:00Z",
    }


def test_scope_approval_is_strictly_loaded_from_external_environment():
    approval = load_postgres_scope_approval(
        _approval_environment(),
        scope_prefix="test_accept_0123456789ab",
    )

    assert approval.approval_id == "approval-2026-08-10"
    assert approval.database_allowlist == frozenset({"interview_test", "audit_test"})
    assert approval.expires_at == datetime(
        2026,
        8,
        11,
        12,
        0,
        tzinfo=timezone.utc,
    )


@pytest.mark.parametrize(
    "missing_name",
    [
        "POSTGRES_ACCEPTANCE_APPROVAL_ID",
        "POSTGRES_ACCEPTANCE_APPROVAL_RECEIPT_SHA256",
        "POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT",
        "POSTGRES_ACCEPTANCE_DATABASE_ALLOWLIST",
        "POSTGRES_ACCEPTANCE_APPROVAL_EXPIRES_AT",
    ],
)
def test_scope_approval_rejects_every_missing_external_binding(missing_name):
    environment = _approval_environment()
    del environment[missing_name]

    with pytest.raises(AcceptanceConfigurationError, match=missing_name):
        load_postgres_scope_approval(
            environment,
            scope_prefix="test_accept_0123456789ab",
        )


def test_scope_approval_rejects_timezone_naive_expiry():
    environment = _approval_environment()
    environment["POSTGRES_ACCEPTANCE_APPROVAL_EXPIRES_AT"] = "2026-08-11T12:00:00"

    with pytest.raises(AcceptanceConfigurationError, match="timezone-aware"):
        load_postgres_scope_approval(
            environment,
            scope_prefix="test_accept_0123456789ab",
        )


def test_receipt_signer_requires_strict_base64_secret_of_minimum_length():
    environment = {
        "EVIDENCE_HMAC_KEY_ID": "acceptance-v1",
        "EVIDENCE_HMAC_SECRET_B64": base64.b64encode(b"k" * 32).decode("ascii"),
    }

    signer = load_receipt_signer(environment)

    assert signer is not None


@pytest.mark.parametrize(
    "secret",
    ["not base64!", base64.b64encode(b"short").decode("ascii")],
)
def test_receipt_signer_rejects_invalid_or_short_secret(secret):
    environment = {
        "EVIDENCE_HMAC_KEY_ID": "acceptance-v1",
        "EVIDENCE_HMAC_SECRET_B64": secret,
    }

    with pytest.raises(AcceptanceConfigurationError):
        load_receipt_signer(environment)
