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
    ProductionBudgetAcceptanceEvidencePayload,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowChangePreflightEvidencePayload,
)
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetObservationEvidencePolicy,
    ProductionBudgetWindowDecisionEvidencePolicy,
)
from scripts import memory_production_budget_shadow_acceptance as acceptance_runner
from scripts.memory_production_budget_shadow_acceptance import (
    build_acceptance_payload,
    evaluate_observation,
    observation_record,
    render_decision,
)
from scripts.memory_production_budget_shadow_observation import (
    build_observation_payload,
    sanitize_aggregate_input,
)


FIXTURE = Path(
    "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
)


def observation():
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return sanitize_aggregate_input(value).artifact


def test_complete_safe_observation_passes_without_authorizing_other_memory():
    record = observation()
    decision = evaluate_observation(record)
    lines = render_decision(decision, record)

    assert decision.status == "PASS"
    assert lines == (
        "PRODUCTION_BUDGET_SHADOW=PASS",
        "OBSERVATION_WINDOW=CLOSED",
        "CONFIGURATION_RESTORED=disabled",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("mandatory_current_content_losses", 1, "MANDATORY_CURRENT_CONTENT_LOSS"),
        ("provider_input_change_count", 1, "PROVIDER_INPUT_CHANGED"),
        ("known_over_budget_provider_calls", 1, "KNOWN_OVER_BUDGET_PROVIDER_CALL"),
        ("privacy_audit_hits", 1, "PRIVACY_AUDIT_HIT"),
        ("approval_current", False, "APPROVAL_NOT_CURRENT"),
        ("revision_match", False, "APPROVED_REVISION_MISMATCH"),
        ("deployment_scope_verified", False, "DEPLOYMENT_SCOPE_MISMATCH"),
        ("budget_config_conflict", True, "BUDGET_CONFIG_CONFLICT"),
        ("other_memory_axis_enabled", True, "OTHER_MEMORY_AXIS_ENABLED"),
        ("data_complete", False, "DURABLE_METRICS_INCOMPLETE"),
        ("shadow_execution_error_count", 1, "SHADOW_EXECUTION_ERROR"),
        (
            "deterministic_interview_regression_count",
            1,
            "DETERMINISTIC_INTERVIEW_REGRESSION",
        ),
        ("configuration_drift_count", 1, "CONFIGURATION_DRIFT"),
        ("rollback_verified", False, "ROLLBACK_NOT_VERIFIED"),
        ("configuration_restored", False, "CONFIGURATION_NOT_RESTORED"),
    ],
)
def test_hard_stop_inputs_block(field, value, code):
    record = observation()
    record[field] = value

    decision = evaluate_observation(record)

    assert decision.status == "BLOCKED"
    assert code in decision.gate_codes
    assert not any("=PASS" in line for line in render_decision(decision, record))


def test_traffic_overshoot_blocks():
    record = observation()
    record["observed_traffic_percent_max"] = 1.01

    decision = evaluate_observation(record)

    assert decision.status == "BLOCKED"
    assert "TRAFFIC_CAP_EXCEEDED" in decision.gate_codes


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("observed_traffic_percent_max", 0.0, "OBSERVED_TRAFFIC_ZERO"),
        ("warmup_duration_minutes", 29.0, "WARMUP_DURATION_INSUFFICIENT"),
        ("warmup_followup_sample_count", 19, "WARMUP_SAMPLE_INSUFFICIENT"),
        ("observation_window_duration_hours", 23.9, "OBSERVATION_WINDOW_TOO_SHORT"),
        ("followup_sample_count", 199, "FOLLOWUP_SAMPLE_INSUFFICIENT"),
        ("control_sample_count", 0, "CONTROL_SAMPLE_MISSING"),
        ("shadow_sample_count", 0, "SHADOW_SAMPLE_MISSING"),
        ("baseline_p95_latency_ms", 0.0, "BASELINE_LATENCY_MISSING"),
    ],
)
def test_insufficient_evidence_requires_a_new_window(field, value, code):
    record = observation()
    record[field] = value

    decision = evaluate_observation(record)
    lines = render_decision(decision, record)

    assert decision.status == "CONTINUE_OBSERVATION"
    assert code in decision.gate_codes
    assert "NEW_APPROVAL_WINDOW_REQUIRED=true" in lines
    assert not any("=PASS" in line for line in lines)


def test_error_and_latency_regressions_apply_after_two_hundred_samples():
    error = observation()
    error["observed_error_rate"] = 0.016
    latency = observation()
    latency["observed_p95_latency_ms"] = 601.0

    error_decision = evaluate_observation(error)
    latency_decision = evaluate_observation(latency)

    assert "FOLLOWUP_ERROR_RATE_REGRESSION" in error_decision.gate_codes
    assert "FOLLOWUP_P95_LATENCY_REGRESSION" in latency_decision.gate_codes


def test_failed_restore_is_reported_truthfully():
    record = observation()
    record["configuration_restored"] = False

    lines = render_decision(evaluate_observation(record), record)

    assert "CONFIGURATION_RESTORED=NOT_VERIFIED" in lines
    assert "CONFIGURATION_RESTORED=disabled" not in lines


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


def closed_window_payload() -> ProductionBudgetWindowDecisionEvidencePayload:
    return ProductionBudgetWindowDecisionEvidencePayload(
        schema_version="production-budget-window-decision-evidence-v1",
        source_preflight_verified=True,
        current_state="CLOSED",
        action="HOLD",
        next_state="CLOSED",
        decision_gate_codes=[],
        configuration_changed=False,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=True,
    )


def test_acceptance_payload_is_strict_and_synthetic_pass_holds():
    observed = build_observation_payload(
        sanitize_aggregate_input(json.loads(FIXTURE.read_text(encoding="utf-8"))),
        preflight=preflight_payload(),
    )
    record = observation_record(observed)
    decision = evaluate_observation(record)
    payload = build_acceptance_payload(
        decision,
        record,
        observation=observed,
        window=closed_window_payload(),
    )

    assert isinstance(payload, ProductionBudgetAcceptanceEvidencePayload)
    assert payload.decision_status == "PASS"
    assert payload.synthetic is True


def test_cli_verifies_observation_and_closed_window_then_writes_acceptance(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"a" * 32
    signer = HmacReceiptSigner(key_id="acceptance-test", secret=secret)
    observed = build_observation_payload(
        sanitize_aggregate_input(json.loads(FIXTURE.read_text(encoding="utf-8"))),
        preflight=preflight_payload(),
    )
    observation_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="production-budget-observation-evidence",
        payload=observed,
        policy_result=ProductionBudgetObservationEvidencePolicy().evaluate(observed),
        producer="tests.observation",
        tool_version="2.0.0",
        revision="abcdef1",
        scope="memory.production-budget-shadow.observation",
        input_manifest=(
            _manifest_item("production-shadow-change-preflight-evidence", "1"),
            _manifest_item("external-production-budget-aggregate", "2"),
        ),
    )
    observation_path = tmp_path / "observation.json"
    AtomicEvidenceWriter().write(observation_path, observation_bundle)
    window = closed_window_payload()
    window_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="production-budget-window-decision-evidence",
        payload=window,
        policy_result=ProductionBudgetWindowDecisionEvidencePolicy().evaluate(window),
        producer="tests.window",
        tool_version="2.0.0",
        revision="abcdef2",
        scope="memory.production-budget-shadow.window-decision",
        input_manifest=(
            _manifest_item("production-shadow-change-preflight-evidence", "3"),
            _manifest_item("external-production-budget-window-state", "4"),
        ),
    )
    window_path = tmp_path / "window.json"
    AtomicEvidenceWriter().write(window_path, window_bundle)
    output = tmp_path / "acceptance.json"
    monkeypatch.setenv("OBSERVATION_EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("WINDOW_EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef3")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "acceptance-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert acceptance_runner.main(
        [
            "--observation-evidence",
            str(observation_path),
            "--window-evidence",
            str(window_path),
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef3",
        expected_scope="memory.production-budget-shadow.acceptance",
    )
    assert isinstance(
        verified.payload,
        ProductionBudgetAcceptanceEvidencePayload,
    )
    assert verified.payload.decision_status == "PASS"
    assert verified.bundle.artifact.verification_status is VerificationStatus.PASS
    assert verified.bundle.artifact.promotion_decision is PromotionDecision.HOLD
    assert len(verified.bundle.artifact.envelope.input_manifest) == 2
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
    assert "PRODUCTION_BUDGET_SHADOW=PASS" in stdout


def test_acceptance_cli_no_longer_consumes_naked_observation_json():
    source = Path(acceptance_runner.__file__).read_text(encoding="utf-8")

    assert 'parser.add_argument("--observation"' not in source
    assert "args.observation.read_text" not in source
