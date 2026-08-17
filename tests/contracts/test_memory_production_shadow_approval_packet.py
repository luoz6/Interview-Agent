from __future__ import annotations

import base64
from datetime import datetime, timezone
import json

import pytest

from app.runtime.config.memory import load_effective_memory_config
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    OperationalShadowEvidencePayload,
)
from contracts.policies import OperationalShadowEvidencePolicy
from scripts import memory_production_shadow_approval_packet as approval_packet
from scripts.memory_production_shadow_approval_packet import (
    ApprovalPacketBlocked,
    PENDING_LINES,
    build_approval_packet,
    evaluate_approval_readiness,
    format_blocked_output,
)


def _operational_payload() -> OperationalShadowEvidencePayload:
    return OperationalShadowEvidencePayload(
        schema_version="operational-shadow-evidence-v1",
        validated_rc_revision="a982b1f",
        validation_revision="ffc58a1",
        environment_category="isolated_staging",
        observation_profile="B",
        full_python_passed=1500,
        postgres_executed=45,
        frontend_modules=4587,
        browser_passed=54,
        budget_followup_samples=300,
        principal_write_samples=300,
        proposal_review_cases=300,
        principal_read_samples=300,
        restore_cycles=3,
        restore_fault_boundaries=6,
        artifacts_audited=9,
        test_listener_residue=0,
        isolated_relation_residue=0,
        private_data_residue=0,
        operational_gates_passed=True,
        safe_defaults=True,
        consume_rejected=True,
        production_approval_required=True,
        long_term_consumption_blocked=True,
        production_observation_not_run=True,
        synthetic=True,
    )


def accepted_inputs():
    return {
        "operational": _operational_payload(),
        "repository": {"safe_defaults": True, "consume_rejected": True},
    }


def test_ready_packet_is_pending_and_requests_budget_shadow_only():
    inputs = accepted_inputs()

    lines = evaluate_approval_readiness(inputs)
    packet = build_approval_packet(inputs)

    assert lines == PENDING_LINES
    assert lines == (
        "MEMORY_PRODUCTION_SHADOW_PACKET=READY_FOR_REVIEW",
        "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
        "APPROVAL_STATUS=PENDING",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert packet.approval_status == "PENDING"
    assert packet.requested_phase == "BUDGET_SHADOW_ONLY"
    assert set(packet.required_approval_roles) == {
        "change_owner",
        "operations",
        "privacy",
        "security",
        "fairness",
    }
    assert packet.configuration_changed is False
    assert packet.production_observation_not_run is True


@pytest.mark.parametrize(
    ("field_name", "gate_code"),
    [
        ("full_python_passed", "OPERATIONAL_FULL_PYTHON_REGRESSION_MISSING"),
        ("postgres_executed", "OPERATIONAL_POSTGRES_REGRESSION_MISSING"),
        ("frontend_modules", "OPERATIONAL_FRONTEND_REGRESSION_MISSING"),
        ("browser_passed", "OPERATIONAL_BROWSER_REGRESSION_MISSING"),
    ],
)
def test_operational_policy_requires_all_regression_gate_counts(
    field_name,
    gate_code,
):
    payload = _operational_payload().model_copy(update={field_name: 0})

    result = OperationalShadowEvidencePolicy().evaluate(payload)

    assert result.verification_status.value == "BLOCKED"
    assert gate_code in result.gate_codes


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (
            lambda value: value.__setitem__(
                "operational",
                value["operational"].model_copy(
                    update={"operational_gates_passed": False}
                ),
            ),
            "OPERATIONAL_SHADOW_NOT_ACCEPTED",
        ),
        (lambda value: value["repository"].update({"safe_defaults": False}), "SAFE_DEFAULTS_CHANGED"),
        (
            lambda value: value.__setitem__(
                "operational",
                value["operational"].model_copy(
                    update={"production_observation_not_run": False}
                ),
            ),
            "PRODUCTION_OBSERVATION_ALREADY_STARTED",
        ),
        (
            lambda value: value.__setitem__(
                "operational",
                value["operational"].model_copy(
                    update={"long_term_consumption_blocked": False}
                ),
            ),
            "CONSUMPTION_BOUNDARY_INVALID",
        ),
    ],
)
def test_any_failed_input_blocks_packet_without_pending_ready_lines(mutator, code):
    inputs = accepted_inputs()
    mutator(inputs)

    with pytest.raises(ApprovalPacketBlocked) as raised:
        evaluate_approval_readiness(inputs)

    assert code in raised.value.codes
    output = format_blocked_output(raised.value.codes)
    assert output[0] == "MEMORY_PRODUCTION_SHADOW_PACKET=BLOCKED"
    assert f"GATE={code}" in output
    assert output[-2:] == (
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert not any("READY_FOR_REVIEW" in line for line in output)


def test_repository_legacy_consume_remains_rejected_and_local_memory_defaults_on():
    config = load_effective_memory_config({})
    assert config.budget.mode == "disabled"
    assert config.compression.mode == "disabled"
    assert config.long_term.mode == "local_consume"
    assert config.long_term.write_shadow_enabled is True
    assert config.long_term.read_shadow_enabled is True
    with pytest.raises(ValueError, match="consume is not supported"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})


def test_packet_pins_production_budget_warmup_and_three_state_tooling():
    packet = build_approval_packet(accepted_inputs())

    assert packet.maximum_traffic_percent == 1.0
    assert packet.initial_warmup_traffic_percent == 0.1
    assert packet.minimum_warmup_minutes == 30
    assert packet.minimum_warmup_followup_samples == 20
    assert packet.minimum_observation_hours == 24
    assert packet.minimum_followup_samples == 200
    assert packet.provider_input_change is False
    assert packet.budget_enforcement is False
    assert packet.principal_write_shadow is False
    assert packet.principal_read_shadow is False


def test_cli_verifies_operational_receipt_and_writes_signed_pending_request(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"p" * 32
    signer = HmacReceiptSigner(key_id="approval-test", secret=secret)
    operational_payload = _operational_payload()
    operational_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="operational-shadow-evidence",
        payload=operational_payload,
        policy_result=OperationalShadowEvidencePolicy().evaluate(
            operational_payload
        ),
        producer="tests.operational",
        tool_version="2.0.0",
        revision="ffc58a1",
        scope="memory.operational-shadow.controlled",
    )
    operational_path = tmp_path / "operational.json"
    AtomicEvidenceWriter().write(operational_path, operational_bundle)
    output = tmp_path / "approval-request.json"
    monkeypatch.setenv("OPERATIONAL_EVIDENCE_REVISION", "ffc58a1")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "approval-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    def load_inputs(**kwargs):
        inputs = accepted_inputs()
        inputs["operational"] = kwargs["operational"]
        return inputs

    monkeypatch.setattr(approval_packet, "load_default_inputs", load_inputs)

    assert approval_packet.main(
        [
            "--operational-evidence",
            str(operational_path),
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
        expected_scope="memory.production-shadow.approval-request",
    )
    assert verified.bundle.artifact.payload_type == (
        "production-shadow-approval-request"
    )
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert len(verified.bundle.artifact.envelope.input_manifest) == 1
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
