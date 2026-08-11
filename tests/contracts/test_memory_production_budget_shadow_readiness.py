from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    ProductionBudgetReadinessEvidencePayload,
    ProductionShadowApprovalRequestPayload,
)
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetReadinessEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
)
from scripts import memory_production_budget_shadow_readiness as readiness
from scripts.memory_production_budget_shadow_readiness import (
    ReadinessBlocked,
    SUCCESS_LINES,
    build_readiness_evidence,
    build_repository_snapshot,
    evaluate_readiness,
    format_blocked_output,
)


def approval_request() -> ProductionShadowApprovalRequestPayload:
    return ProductionShadowApprovalRequestPayload(
        schema_version="production-shadow-approval-request-v1",
        validated_rc_revision="a982b1f",
        validation_revision="ffc58a1",
        evidence_environment="isolated_staging",
        evidence_profile="B",
        requested_phase="BUDGET_SHADOW_ONLY",
        approval_status="PENDING",
        required_approval_roles=[
            "change_owner",
            "operations",
            "privacy",
            "security",
            "fairness",
        ],
        maximum_traffic_percent=1.0,
        initial_warmup_traffic_percent=0.1,
        minimum_warmup_minutes=30,
        minimum_warmup_followup_samples=20,
        minimum_observation_hours=24,
        minimum_followup_samples=200,
        provider_input_change=False,
        budget_enforcement=False,
        compression_consumption=False,
        principal_write_shadow=False,
        principal_read_shadow=False,
        principal_memory_consumption=False,
        production_migration=False,
        configuration_changed=False,
        production_observation_not_run=True,
        long_term_consumption_blocked=True,
        synthetic=True,
    )


def ready_snapshot() -> dict[str, object]:
    return {
        "validated_revision": "a" * 40,
        "contracts_present": True,
        "offline_source_audit": True,
        "observation_probe_status": "PASS",
        "window_probe_action": "START_WARM_UP",
        "safe_defaults": True,
        "consume_rejected": True,
        "approval_request_verified": True,
        "approval_request_safe": True,
        "hard_stop_clear": True,
        "production_observation_not_run": True,
        "configuration_changed": False,
        "external_approval_input_used": False,
        "pending_example_gate_codes": [
            "APPROVAL_RECORD_NOT_EXTERNAL",
            "APPROVAL_STATUS_NOT_APPROVED",
        ],
    }


def test_ready_snapshot_has_exact_pending_output_and_strict_payload():
    snapshot = ready_snapshot()
    payload = build_readiness_evidence(
        snapshot,
        approval_request=approval_request(),
    )
    result = ProductionBudgetReadinessEvidencePolicy().evaluate(payload)

    assert evaluate_readiness(snapshot) == SUCCESS_LINES
    assert SUCCESS_LINES == (
        "PRODUCTION_BUDGET_SHADOW_TOOLING=READY_FOR_REVIEW",
        "APPROVAL_STATUS=PENDING",
        "CHANGE_PREFLIGHT=BLOCKED",
        "CONFIGURATION_CHANGED=false",
        "PRODUCTION_OBSERVATION=NOT_RUN",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )
    assert isinstance(payload, ProductionBudgetReadinessEvidencePayload)
    assert result.verification_status is VerificationStatus.PASS
    assert result.promotion_decision is PromotionDecision.HOLD
    assert result.gate_codes == ()


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("contracts_present", False, "PRODUCTION_CONTRACTS_MISSING"),
        ("offline_source_audit", False, "PRODUCTION_TOOLING_NOT_OFFLINE"),
        (
            "observation_probe_status",
            "BLOCKED",
            "PRODUCTION_OBSERVATION_PROBE_NOT_GREEN",
        ),
        (
            "window_probe_action",
            "STOP_NOW",
            "PRODUCTION_WINDOW_PROBE_NOT_GREEN",
        ),
        ("safe_defaults", False, "SAFE_DEFAULTS_CHANGED"),
        ("consume_rejected", False, "CONSUME_NOT_REJECTED"),
        (
            "approval_request_verified",
            False,
            "PRODUCTION_APPROVAL_REQUEST_UNVERIFIED",
        ),
        (
            "approval_request_safe",
            False,
            "PRODUCTION_APPROVAL_REQUEST_UNSAFE",
        ),
        ("hard_stop_clear", False, "SHADOW_HARD_STOP_ACTIVE"),
        (
            "production_observation_not_run",
            False,
            "PRODUCTION_OBSERVATION_ALREADY_STARTED",
        ),
        (
            "configuration_changed",
            True,
            "READINESS_CONFIGURATION_CHANGED",
        ),
        (
            "external_approval_input_used",
            True,
            "EXTERNAL_APPROVAL_INPUT_NOT_ALLOWED",
        ),
        (
            "pending_example_gate_codes",
            [],
            "PENDING_EXAMPLE_FAIL_CLOSED_INVALID",
        ),
        ("validated_revision", "invalid", "VALIDATED_REVISION_INVALID"),
    ],
)
def test_any_failed_readiness_gate_blocks_without_ready_line(field, value, code):
    snapshot = ready_snapshot()
    snapshot[field] = value

    with pytest.raises(ReadinessBlocked) as raised:
        evaluate_readiness(snapshot)

    assert code in raised.value.codes
    lines = format_blocked_output(raised.value.codes)
    assert not any("READY_FOR_REVIEW" in line for line in lines)
    assert "CONFIGURATION_CHANGED=false" in lines
    assert "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED" in lines


def test_payload_schema_and_policy_reject_approved_private_or_mutated_state():
    payload = build_readiness_evidence(
        ready_snapshot(),
        approval_request=approval_request(),
    )
    approved = deepcopy(payload.model_dump(mode="json"))
    approved["approval_status"] = "APPROVED"
    with pytest.raises(ValidationError):
        ProductionBudgetReadinessEvidencePayload.model_validate(approved)

    private = deepcopy(payload.model_dump(mode="json"))
    private["principal_id"] = "private"
    with pytest.raises(ValidationError):
        ProductionBudgetReadinessEvidencePayload.model_validate(private)

    changed = payload.model_copy(update={"configuration_changed": True})
    result = ProductionBudgetReadinessEvidencePolicy().evaluate(changed)
    assert result.verification_status is VerificationStatus.BLOCKED
    assert "READINESS_CONFIGURATION_CHANGED" in result.gate_codes


def test_current_repository_snapshot_uses_verified_request_without_external_input(
    monkeypatch,
):
    monkeypatch.setattr(readiness, "_git_revision", lambda: "a" * 40)
    snapshot = build_repository_snapshot(
        approval_request(),
        approval_request_verified=True,
    )

    assert snapshot["approval_request_verified"] is True
    assert snapshot["approval_request_safe"] is True
    assert snapshot["external_approval_input_used"] is False
    assert snapshot["pending_example_gate_codes"] == [
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
    ]
    assert evaluate_readiness(snapshot) == SUCCESS_LINES


def test_cli_verifies_approval_receipt_and_writes_signed_readiness(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"r" * 32
    signer = HmacReceiptSigner(key_id="readiness-test", secret=secret)
    request = approval_request()
    request_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="production-shadow-approval-request",
        payload=request,
        policy_result=ProductionShadowApprovalRequestPolicy().evaluate(request),
        producer="tests.approval-request",
        tool_version="2.0.0",
        revision="abcdef1",
        scope="memory.production-shadow.approval-request",
    )
    request_path = tmp_path / "approval-request.json"
    AtomicEvidenceWriter().write(request_path, request_bundle)
    output = tmp_path / "readiness.json"
    monkeypatch.setenv("APPROVAL_REQUEST_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "readiness-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )
    monkeypatch.setattr(readiness, "_git_revision", lambda: "abcdef3")

    assert readiness.main(
        [
            "--approval-request",
            str(request_path),
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef2",
        expected_scope="memory.production-budget-shadow.readiness",
    )
    assert isinstance(
        verified.payload,
        ProductionBudgetReadinessEvidencePayload,
    )
    assert verified.bundle.artifact.payload_type == (
        "production-budget-readiness-evidence"
    )
    assert verified.bundle.artifact.verification_status is VerificationStatus.PASS
    assert verified.bundle.artifact.promotion_decision is PromotionDecision.HOLD
    assert len(verified.bundle.artifact.envelope.input_manifest) == 1
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout


def test_cli_rejects_legacy_unsigned_request_without_writing_output(
    monkeypatch,
    tmp_path,
    capsys,
):
    request_path = tmp_path / "legacy-request.json"
    request_path.write_text(
        json.dumps(approval_request().model_dump(mode="json")),
        encoding="utf-8",
    )
    output = tmp_path / "readiness.json"
    monkeypatch.setenv("APPROVAL_REQUEST_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "readiness-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(b"r" * 32).decode("ascii"),
    )

    assert readiness.main(
        [
            "--approval-request",
            str(request_path),
            "--output",
            str(output),
        ]
    ) == 1
    assert not output.exists()
    assert "GATE=PRODUCTION_APPROVAL_REQUEST_UNVERIFIED" in capsys.readouterr().out
