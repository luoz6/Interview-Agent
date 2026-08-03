from copy import deepcopy

import pytest

from scripts.memory_production_budget_shadow_readiness import (
    ReadinessBlocked,
    SUCCESS_LINES,
    build_readiness_evidence,
    build_repository_snapshot,
    evaluate_readiness,
    format_blocked_output,
    validate_readiness_evidence,
)


def ready_snapshot():
    return {
        "validated_revision": "a" * 40,
        "contracts_present": True,
        "offline_source_audit": True,
        "observation_probe_status": "PASS",
        "window_probe_action": "START_WARM_UP",
        "safe_defaults": True,
        "consume_rejected": True,
        "approval_packet_ready": True,
        "hard_stop_clear": True,
        "production_observation_not_run": True,
        "configuration_changed": False,
        "external_approval_input_used": False,
        "pending_example_gate_codes": [
            "APPROVAL_RECORD_NOT_EXTERNAL",
            "APPROVAL_STATUS_NOT_APPROVED",
        ],
    }


def test_ready_snapshot_has_exact_pending_output_and_safe_evidence():
    snapshot = ready_snapshot()
    evidence = build_readiness_evidence(snapshot)

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
    validate_readiness_evidence(evidence)


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
        ("approval_packet_ready", False, "APPROVAL_PACKET_NOT_READY"),
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


def test_evidence_rejects_approved_private_or_mutated_state():
    evidence = build_readiness_evidence(ready_snapshot())
    approved = deepcopy(evidence)
    approved["approval_status"] = "APPROVED"
    with pytest.raises(RuntimeError, match="pending"):
        validate_readiness_evidence(approved)
    private = deepcopy(evidence)
    private["principal_id"] = "private"
    with pytest.raises(RuntimeError, match="private"):
        validate_readiness_evidence(private)
    changed = deepcopy(evidence)
    changed["configuration_changed"] = True
    with pytest.raises(RuntimeError, match="configuration"):
        validate_readiness_evidence(changed)


def test_current_repository_snapshot_is_ready_and_does_not_use_external_input():
    snapshot = build_repository_snapshot()

    assert snapshot["external_approval_input_used"] is False
    assert snapshot["pending_example_gate_codes"] == [
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
    ]
    assert evaluate_readiness(snapshot) == SUCCESS_LINES
