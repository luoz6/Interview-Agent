from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from app.ports.postgres_scope import PostgresCleanupReceipt
from contracts.evidence import EvidenceRegistry, EvidenceVerifier, HmacReceiptSigner
from scripts import stage43b_recovery_acceptance as recovery
from scripts.stage43b_recovery_acceptance import (
    CHECKS,
    AcceptanceFailure,
    build_recovery_evidence,
    run_acceptance,
)


class FakeAdapter:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.cleaned = False

    def setup(self):
        self.calls.append("setup")

    def run_check(self, name):
        self.calls.append(name)
        if name == self.failure:
            raise AcceptanceFailure("check_failed")
        return {"status": "PASS"}

    def cleanup(self):
        self.cleaned = True


def cleanup_receipt(**overrides):
    values = {
        "schema_version": "postgres-cleanup-receipt-v1",
        "approval_id": "stage43b-test-approval",
        "approval_receipt_sha256": "a" * 64,
        "target_fingerprint": "b" * 64,
        "scope_prefix": "test_s43b_0123456789ab",
        "ownership_verified": True,
        "target_verified": True,
        "resources_examined": 8,
        "resources_removed": 8,
        "residue_count": 0,
        "cleanup_started_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "cleanup_finished_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "receipt_sha256": "c" * 64,
    }
    values.update(overrides)
    return PostgresCleanupReceipt(**values)


def test_runner_executes_every_named_check_and_cleans_up():
    adapter = FakeAdapter()

    result = run_acceptance(adapter)

    assert result["status"] == "PASS"
    assert tuple(result["checks"]) == CHECKS
    assert adapter.cleaned is True


def test_failed_check_returns_stable_code_and_cleans_up():
    adapter = FakeAdapter(failure=CHECKS[2])

    result = run_acceptance(adapter)

    assert result == {
        "status": "FAIL",
        "error_code": "check_failed",
        "failed_check": CHECKS[2],
        "checks": {
            CHECKS[0]: {"status": "PASS"},
            CHECKS[1]: {"status": "PASS"},
        },
    }
    assert adapter.cleaned is True


def test_recovery_payload_uses_strict_gate_code_and_holds_synthetic_result():
    result = run_acceptance(FakeAdapter())
    payload = build_recovery_evidence(
        result,
        cleanup_receipt=cleanup_receipt(),
        synthetic=True,
    )

    policy_result = recovery.Stage43bRecoveryEvidencePolicy().evaluate(payload)

    assert payload.checks_passed == len(CHECKS)
    assert payload.cleanup_ownership_verified is True
    assert payload.cleanup_target_verified is True
    assert payload.cleanup_residue_count == 0
    assert policy_result.verification_status.value == "PASS"
    assert policy_result.promotion_decision.value == "HOLD"


def test_recovery_policy_blocks_missing_or_residual_cleanup_receipt():
    result = run_acceptance(FakeAdapter())

    missing = build_recovery_evidence(result, synthetic=True)
    residual = build_recovery_evidence(
        result,
        cleanup_receipt=cleanup_receipt(residue_count=1),
        synthetic=True,
    )

    missing_result = recovery.Stage43bRecoveryEvidencePolicy().evaluate(missing)
    residual_result = recovery.Stage43bRecoveryEvidencePolicy().evaluate(residual)
    assert "STAGE43B_CLEANUP_RECEIPT_MISSING" in missing_result.gate_codes
    assert "STAGE43B_TARGET_IDENTITY_MISSING" in missing_result.gate_codes
    assert "STAGE43B_CLEANUP_RESIDUE" in residual_result.gate_codes


def test_cli_writes_signed_synthetic_recovery_evidence(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"b" * 32
    output = tmp_path / "recovery-evidence.json"
    monkeypatch.setattr(
        recovery,
        "PostgresCeleryAcceptance",
        lambda **kwargs: FakeAdapter(),
    )
    receipt = cleanup_receipt()

    @contextmanager
    def owned_scope(**kwargs):
        yield SimpleNamespace(lease=SimpleNamespace(cleanup_receipt=receipt))

    monkeypatch.setattr(recovery, "approved_postgres_scope", owned_scope)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://runtime.invalid/interview")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "stage43b-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert recovery.main(["--synthetic", "--output", str(output)]) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=HmacReceiptSigner(
            key_id="stage43b-test",
            secret=secret,
        ),
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef1",
        expected_scope="stage43b.recovery.acceptance",
    )
    assert verified.bundle.artifact.payload_type == "stage43b-recovery-evidence"
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert verified.payload.cleanup_receipt_sha256 == receipt.receipt_sha256
    assert verified.payload.target_fingerprint == receipt.target_fingerprint
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
