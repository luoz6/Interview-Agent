from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.knowledge_rocketmq_v4_target_preflight import (
    EXPECTED_CORPUS_HASH,
    evaluate_target_preflight,
)


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
FINGERPRINT = "f" * 64


def target(**overrides):
    value = {
        "reachable": True,
        "identity": {
            "database_name": "interview_test",
            "fingerprint": FINGERPRINT,
            "transaction_read_only": "on",
        },
        "vector_extension_version": "0.8.2",
        "tables": {
            "versions_exists": True,
            "releases_exists": True,
        },
        "releases": [],
        "version_row_counts": {},
        "active_corpus_version": None,
        "active_manifest_sha256": None,
    }
    value.update(overrides)
    return value


def approved_environment(**overrides):
    value = {
        "POSTGRES_TEST_APPROVAL_ID": "approval-1",
        "POSTGRES_TEST_APPROVAL_RECEIPT_SHA256": "a" * 64,
        "POSTGRES_TEST_APPROVED_FINGERPRINT": FINGERPRINT,
        "POSTGRES_TEST_DATABASE_ALLOWLIST": "interview_test",
        "POSTGRES_TEST_APPROVAL_EXPIRES_AT": (
            NOW + timedelta(hours=1)
        ).isoformat(),
        "EMBEDDING_PROVIDER": "siliconflow",
        "EMBEDDING_MODEL_NAME": "BAAI/bge-m3",
        "EMBEDDING_MODEL_REVISION": "siliconflow-2026-08-13",
        "SILICONFLOW_API_KEY": "configured-secret",
        "RUN_KNOWLEDGE_ROCKETMQ_V4_LOAD": "1",
    }
    value.update(overrides)
    return value


def test_target_preflight_passes_only_with_all_external_gates():
    result = evaluate_target_preflight(
        target(),
        approved_environment(),
        now=NOW,
    )

    assert result["passed"] is True
    assert result["write_ready"] is True
    assert result["failure_reasons"] == []
    assert result["approval"]["valid"] is True
    assert result["embedding"]["fixed_identity"] is True
    assert result["operator_authorized"] is True


def test_target_preflight_reports_missing_approval_embedding_and_authorization():
    result = evaluate_target_preflight(target(), {}, now=NOW)

    assert result["passed"] is False
    assert result["failure_reasons"] == [
        "POSTGRES_SCOPE_APPROVAL_REQUIRED",
        "FIXED_EMBEDDING_IDENTITY_REQUIRED",
        "ROCKETMQ_V4_LOAD_AUTHORIZATION_REQUIRED",
    ]
    assert set(result["approval"]["missing_or_invalid"]) == {
        "POSTGRES_TEST_APPROVAL_ID",
        "POSTGRES_TEST_APPROVAL_RECEIPT_SHA256",
        "POSTGRES_TEST_APPROVED_FINGERPRINT",
        "POSTGRES_TEST_DATABASE_ALLOWLIST",
        "POSTGRES_TEST_APPROVAL_EXPIRES_AT",
    }


def test_target_preflight_rejects_current_revision_and_target_mismatch():
    environment = approved_environment(
        EMBEDDING_MODEL_REVISION="siliconflow-current",
        POSTGRES_TEST_APPROVED_FINGERPRINT="e" * 64,
    )

    result = evaluate_target_preflight(target(), environment, now=NOW)

    assert result["passed"] is False
    assert "POSTGRES_SCOPE_APPROVAL_REQUIRED" in result["failure_reasons"]
    assert "FIXED_EMBEDDING_IDENTITY_REQUIRED" in result["failure_reasons"]
    assert result["approval"]["missing_or_invalid"] == [
        "POSTGRES_TEST_TARGET_FINGERPRINT_MISMATCH"
    ]


def test_target_preflight_requires_review_before_replacing_active_corpus():
    result = evaluate_target_preflight(
        target(
            active_corpus_version="memory-p1-zh-v3",
            active_manifest_sha256="b" * 64,
        ),
        approved_environment(),
        now=NOW,
    )

    assert result["passed"] is False
    assert result["activation_would_replace_existing"] is True
    assert "ACTIVE_CORPUS_REPLACEMENT_REQUIRES_REVIEW" in result[
        "failure_reasons"
    ]


def test_target_preflight_accepts_already_active_expected_identity():
    result = evaluate_target_preflight(
        target(
            active_corpus_version="memory-p1-zh-v4",
            active_manifest_sha256=EXPECTED_CORPUS_HASH,
        ),
        approved_environment(),
        now=NOW,
    )

    assert result["passed"] is True
    assert result["active_is_expected"] is True
    assert result["activation_would_replace_existing"] is False
