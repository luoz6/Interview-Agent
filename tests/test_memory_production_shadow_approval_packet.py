from copy import deepcopy
from pathlib import Path

import pytest

from app.services.memory_config import load_effective_memory_config
from scripts.memory_production_shadow_approval_packet import (
    ApprovalPacketBlocked,
    PENDING_LINES,
    build_approval_packet,
    evaluate_approval_readiness,
    format_blocked_output,
    validate_packet_artifact,
)


def accepted_inputs():
    return {
        "operational": {
            "accepted": True,
            "validated_rc_revision": "a982b1f",
            "validation_revision": "ffc58a1",
            "environment_category": "isolated_staging",
            "observation_profile": "B",
            "aggregate_gates": {
                "budget_shadow": "PASS",
                "principal_write_shadow": "PASS",
                "principal_read_zero_injection": "PASS",
                "consent_deletion_restore": "PASS",
                "privacy_security_fairness_firewall": "PASS",
            },
            "cleanup": {
                "isolated_relation_residue": 0,
                "private_data_residue": 0,
                "test_listeners": 0,
            },
            "safe_defaults": {
                "budget": "disabled",
                "compression": "disabled",
                "principal_memory": "disabled",
                "trusted_local_api": "disabled",
                "consume_rejected": True,
            },
            "production_shadow_approval": "REQUIRED",
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        },
        "status": {
            "automatic_stop": {
                "triggered": False,
                "gate_codes": [],
                "deterministic_path_available": True,
            },
            "hold_codes": [],
            "configuration_changed": False,
            "configuration_mutation_available": False,
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        },
        "security": {
            "review_status": "PASS",
            "artifact_violations": 0,
            "hard_stop_count": 0,
            "knowledge_firewall_violations": 0,
            "protected_taxonomy_hits": 0,
            "public_knowledge_unchanged": True,
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        },
        "regression": {
            "clean_detached_worktree": True,
            "full_python": {"passed": True, "failed": 0},
            "pg_runtime": {"passed": True, "failed": 0},
            "frontend_build": {"passed": True},
            "full_browser": {"passed": True, "failed": 0, "scope": "full"},
            "compileall": {"passed": True},
            "diff_check": {"passed": True},
            "cleanup": {
                "test_listeners": 0,
                "isolated_test_relation_residue": 0,
            },
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        },
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
    assert packet["approval_status"] == "PENDING"
    assert packet["requested_phase"] == "BUDGET_SHADOW_ONLY"
    assert all(value == "PENDING" for value in packet["required_approvals"].values())
    assert packet["configuration_changed"] is False
    assert packet["production_observation"] == "NOT_RUN"
    validate_packet_artifact(packet)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value["operational"].update({"accepted": False}), "OPERATIONAL_SHADOW_NOT_ACCEPTED"),
        (lambda value: value["status"]["automatic_stop"].update({"triggered": True}), "SHADOW_HARD_STOP_ACTIVE"),
        (lambda value: value["security"].update({"review_status": "BLOCKED"}), "SECURITY_REVIEW_NOT_GREEN"),
        (lambda value: value["regression"]["full_python"].update({"failed": 1}), "REGRESSION_NOT_GREEN"),
        (lambda value: value["repository"].update({"safe_defaults": False}), "SAFE_DEFAULTS_CHANGED"),
        (lambda value: value["operational"].update({"production_observation": "PASS"}), "PRODUCTION_OBSERVATION_ALREADY_STARTED"),
        (lambda value: value["operational"].update({"long_term_memory_consumption": "READY"}), "CONSUMPTION_BOUNDARY_INVALID"),
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


def test_packet_validator_rejects_approved_state_private_data_or_mutation():
    packet = build_approval_packet(accepted_inputs())
    approved = deepcopy(packet)
    approved["approval_status"] = "APPROVED"
    with pytest.raises(RuntimeError, match="pending"):
        validate_packet_artifact(approved)

    private = deepcopy(packet)
    private["principal_id"] = "private"
    with pytest.raises(RuntimeError, match="private"):
        validate_packet_artifact(private)

    changed = deepcopy(packet)
    changed["configuration_changed"] = True
    with pytest.raises(RuntimeError, match="configuration"):
        validate_packet_artifact(changed)


def test_repository_consume_remains_rejected_and_all_modes_default_disabled():
    config = load_effective_memory_config({})
    assert config.budget.mode == "disabled"
    assert config.compression.mode == "disabled"
    assert config.long_term.mode == "disabled"
    assert config.long_term.write_shadow_enabled is False
    assert config.long_term.read_shadow_enabled is False
    with pytest.raises(ValueError, match="consume is not supported"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})


def test_approval_request_is_pending_and_requires_all_named_owners():
    text = Path("docs/memory-production-shadow-approval-request.md").read_text(
        encoding="utf-8"
    )
    for line in PENDING_LINES:
        assert line in text
    for owner in (
        "Change owner",
        "Operations / rollback owner",
        "Privacy owner",
        "Security owner",
        "Fairness owner",
    ):
        assert owner in text
    assert "Approval status:** Pending" in text
    assert "Approval status:** Approved" not in text
    assert "Budget Shadow only" in text


def test_runbook_has_hard_hold_point_one_axis_and_safe_rollback():
    text = " ".join(
        Path("docs/memory-production-budget-shadow-runbook.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for phrase in (
        "It must not be used while `APPROVAL_STATUS=PENDING`",
        "MEMORY_BUDGET_MODE=shadow",
        "MEMORY_BUDGET_SHADOW_ENABLED=true",
        "MEMORY_BUDGET_MODE=disabled",
        "MEMORY_BUDGET_SHADOW_ENABLED=false",
        "MEMORY_COMPRESSION_MODE=disabled",
        "MEMORY_LONG_TERM_MODE=disabled",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    ):
        assert phrase in text
    assert "Apply the change through the approved deployment system" in text
