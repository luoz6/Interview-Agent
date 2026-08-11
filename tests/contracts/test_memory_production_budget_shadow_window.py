from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    InputArtifact,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowChangePreflightEvidencePayload,
)
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import ProductionShadowChangePreflightEvidencePolicy
from scripts import memory_production_budget_shadow_window as window_runner
from scripts.memory_production_budget_shadow_window import (
    WindowInputBlocked,
    build_decision_artifact,
    build_decision_payload,
    decide_window_action,
    validate_window_input,
)


def state_input(state="PREFLIGHT_VERIFIED"):
    return {
        "schema_version": "memory-production-budget-shadow-window-input-v1",
        "state": state,
        "approval_record_verified": True,
        "approval_current": True,
        "inside_approved_window": True,
        "revision_match": True,
        "deployment_scope_verified": True,
        "configuration_match": True,
        "configuration_single_axis": True,
        "other_memory_axis_enabled": False,
        "data_complete": True,
        "max_consecutive_missing_minute_buckets": 0,
        "hard_stop_count": 0,
        "approved_traffic_percent": 1.0,
        "observed_traffic_percent": 0.1,
        "warmup_duration_minutes": 0.0,
        "warmup_followup_sample_count": 0,
        "scheduled_end_reached": False,
        "manual_stop_requested": False,
    }


def test_preflight_verified_starts_warmup_without_changing_configuration():
    value = state_input()
    decision = decide_window_action(value)
    artifact = build_decision_artifact(value, decision)

    assert decision.action == "START_WARM_UP"
    assert decision.next_state == "WARM_UP"
    assert artifact["configuration_changed"] is False
    assert artifact["long_term_memory_consumption"] == "BLOCKED"


def test_warmup_requires_both_duration_and_samples_before_ramp():
    value = state_input("WARM_UP")
    duration_only = deepcopy(value)
    duration_only["warmup_duration_minutes"] = 30.0
    samples_only = deepcopy(value)
    samples_only["warmup_followup_sample_count"] = 20
    complete = deepcopy(value)
    complete["warmup_duration_minutes"] = 30.0
    complete["warmup_followup_sample_count"] = 20

    assert decide_window_action(duration_only).action == "KEEP_WARM_UP"
    assert decide_window_action(samples_only).action == "KEEP_WARM_UP"
    assert decide_window_action(complete).action == "RAMP_TO_APPROVED_CAP"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("approval_current", False, "APPROVAL_NOT_CURRENT"),
        ("inside_approved_window", False, "APPROVAL_NOT_CURRENT"),
        ("revision_match", False, "APPROVED_REVISION_MISMATCH"),
        ("deployment_scope_verified", False, "DEPLOYMENT_SCOPE_MISMATCH"),
        ("configuration_match", False, "CONFIGURATION_DRIFT"),
        ("configuration_single_axis", False, "CONFIGURATION_DRIFT"),
        ("other_memory_axis_enabled", True, "OTHER_MEMORY_AXIS_ENABLED"),
        ("data_complete", False, "DURABLE_METRICS_INCOMPLETE"),
        ("hard_stop_count", 1, "HARD_STOP_ACTIVE"),
    ],
)
def test_runtime_safety_failure_stops_immediately(field, value, code):
    item = state_input("OBSERVING")
    item[field] = value

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert decision.next_state == "STOPPING"
    assert code in decision.gate_codes


def test_two_missing_minute_buckets_stop():
    item = state_input("OBSERVING")
    item["max_consecutive_missing_minute_buckets"] = 2

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert "DURABLE_METRICS_INCOMPLETE" in decision.gate_codes


def test_traffic_above_approved_cap_stops():
    item = state_input("OBSERVING")
    item["observed_traffic_percent"] = 1.01

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert "TRAFFIC_CAP_EXCEEDED" in decision.gate_codes


def test_manual_stop_precedes_scheduled_close():
    item = state_input("OBSERVING")
    item["manual_stop_requested"] = True
    item["scheduled_end_reached"] = True

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert decision.gate_codes == ("MANUAL_STOP",)


def test_scheduled_end_closes_even_when_healthy():
    item = state_input("OBSERVING")
    item["scheduled_end_reached"] = True

    decision = decide_window_action(item)

    assert decision.action == "CLOSE_SCHEDULED"
    assert decision.next_state == "STOPPING"


def test_closed_state_cannot_return_to_observing():
    item = state_input("CLOSED")
    item["scheduled_end_reached"] = True

    decision = decide_window_action(item)

    assert decision.action == "HOLD"
    assert decision.next_state == "CLOSED"


def test_pending_approval_holds_and_reports_missing_approval():
    item = state_input("PENDING_APPROVAL")
    item["approval_record_verified"] = False
    item["approval_current"] = False

    decision = decide_window_action(item)

    assert decision.action == "HOLD"
    assert "APPROVAL_RECORD_NOT_VERIFIED" in decision.gate_codes
    assert "APPROVAL_NOT_CURRENT" in decision.gate_codes


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value.update({"state": "RUNNING"}), "WINDOW_STATE_INVALID"),
        (
            lambda value: value.update({"approved_traffic_percent": 2.0}),
            "APPROVED_TRAFFIC_PERCENT_INVALID",
        ),
        (
            lambda value: value.update({"principal_id": "private"}),
            "WINDOW_INPUT_FIELD_SET_INVALID",
        ),
    ],
)
def test_invalid_window_input_is_blocked(mutator, code):
    item = state_input()
    mutator(item)

    with pytest.raises(WindowInputBlocked) as raised:
        validate_window_input(item)

    assert code in raised.value.codes


def preflight_payload() -> ProductionShadowChangePreflightEvidencePayload:
    return ProductionShadowChangePreflightEvidencePayload(
        schema_version="production-shadow-change-preflight-evidence-v1",
        validated_revision="a" * 40,
        validated_rc_revision="a982b1f",
        validation_revision="ffc58a1",
        approval_request_verified=True,
        readiness_verified=True,
        approval_record_verified=True,
        approval_roles_verified=5,
        record_is_external=True,
        record_hash_match=True,
        revision_match=True,
        deployment_scope_match=True,
        requested_phase="BUDGET_SHADOW_ONLY",
        traffic_percent=1.0,
        window_duration_hours=24,
        configuration_changed=False,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _manifest_item(path: str, character: str) -> InputArtifact:
    return InputArtifact(
        path=path,
        sha256=character * 64,
        receipt_sha256=character * 64,
        size_bytes=1,
        media_type="application/json",
    )


def test_decision_payload_is_strict_and_synthetic_decision_holds():
    value = state_input()
    decision = decide_window_action(value)
    payload = build_decision_payload(
        value,
        decision,
        preflight=preflight_payload(),
    )

    assert isinstance(payload, ProductionBudgetWindowDecisionEvidencePayload)
    assert payload.action == "START_WARM_UP"
    assert payload.synthetic is True


def test_cli_verifies_preflight_and_writes_protected_window_decision(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"w" * 32
    signer = HmacReceiptSigner(key_id="window-test", secret=secret)
    preflight = preflight_payload()
    preflight_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="production-shadow-change-preflight-evidence",
        payload=preflight,
        policy_result=ProductionShadowChangePreflightEvidencePolicy().evaluate(
            preflight
        ),
        producer="tests.preflight",
        tool_version="2.0.0",
        revision="abcdef1",
        scope="memory.production-shadow.change-preflight",
        input_manifest=(
            _manifest_item("production-shadow-approval-request", "1"),
            _manifest_item("production-budget-readiness-evidence", "2"),
            _manifest_item("external-production-shadow-approval-record", "3"),
        ),
    )
    preflight_path = tmp_path / "preflight.json"
    AtomicEvidenceWriter().write(preflight_path, preflight_bundle)
    state = state_input()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    output = tmp_path / "window.json"
    monkeypatch.setenv("PREFLIGHT_EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "window-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert window_runner.main(
        [
            "--preflight-evidence",
            str(preflight_path),
            "--state-input",
            str(state_path),
            "--expected-state-sha256",
            canonical_sha256(state),
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
        expected_scope="memory.production-budget-shadow.window-decision",
    )
    assert isinstance(
        verified.payload,
        ProductionBudgetWindowDecisionEvidencePayload,
    )
    assert verified.payload.action == "START_WARM_UP"
    assert verified.bundle.artifact.verification_status is VerificationStatus.PASS
    assert verified.bundle.artifact.promotion_decision is PromotionDecision.HOLD
    assert len(verified.bundle.artifact.envelope.input_manifest) == 2
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
