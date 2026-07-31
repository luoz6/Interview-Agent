from copy import deepcopy
import json

import pytest

from app.services.memory_config import load_effective_memory_config
from scripts.memory_operational_shadow_acceptance import (
    AcceptanceBlocked,
    SUCCESS_LINES,
    build_acceptance_evidence,
    evaluate_operational_shadow,
    format_blocked_output,
    validate_acceptance_artifact,
)


def accepted_bundle():
    return {
        "rc": {
            "validated_rc_revision": "a982b1f",
            "release_candidate": {
                "passed": True,
                "clean_detached_worktree": True,
            },
            "full_python": {"passed": True, "passed_count": 1450, "skipped": 162, "failed": 0},
            "pg_runtime": {"passed": True, "executed": 43, "deselected": 1569, "cleanup_verified": True},
            "frontend_build": {"passed": True, "modules_transformed": 4587},
            "full_browser": {"passed": True, "scope": "full", "passed_count": 54, "skipped": 22, "failed": 0},
            "durable_metrics": {"passed": True, "store_kind": "postgres_aggregate", "data_complete": True},
            "safe_defaults": {"passed": True, "consume_rejected": True},
            "production_observation": "NOT_RUN",
        },
        "regression": {
            "validated_revision": "abcdef1",
            "clean_detached_worktree": True,
            "full_python": {"passed": True, "passed_count": 1500, "skipped": 160, "failed": 0},
            "pg_runtime": {"passed": True, "executed": 45, "deselected": 1600, "failed": 0},
            "frontend_build": {"passed": True, "modules_transformed": 4587},
            "full_browser": {"passed": True, "scope": "full", "passed_count": 54, "skipped": 22, "failed": 0},
            "compileall": {"passed": True},
            "diff_check": {"passed": True},
            "cleanup": {"test_listeners": 0, "isolated_test_relation_residue": 0},
        },
        "staging_text": "STAGING_PREFLIGHT=PASS\nMIGRATION_SCOPE=ISOLATED\nROLLBACK_DRILL=PASS",
        "budget_text": "BUDGET_SHADOW_STAGING=PASS\nBUDGET_ENFORCEMENT=BLOCKED",
        "budget": {
            "profile": "B",
            "followup_sample_count": 300,
            "language_sample_counts": {"en": 100, "mixed": 100, "zh_hans": 100},
            "mandatory_current_content_losses": 0,
            "known_over_budget_provider_calls": 0,
            "data_complete": True,
            "cleanup_residue": 0,
            "rollback_verified": True,
            "production_observation": "NOT_RUN",
        },
        "write": {
            "sample_count": 300,
            "hard_invariants": {"automatic_active": 0, "cross_principal_write": 0},
            "cleanup_residue": 0,
            "rollback_verified": True,
            "production_observation": "NOT_RUN",
        },
        "quality": {
            "reviewed_count": 300,
            "privacy_sensitive_count": 0,
            "stale_source_accepted_count": 0,
            "quality_gate": "PASS",
            "production_observation": "NOT_RUN",
        },
        "read": {
            "sample_count": 300,
            "hard_invariants": {"provider_context_mutation": 0, "cross_principal_selected": 0},
            "provider_isolation": "PASS",
            "read_shadow_gate": "PASS",
            "cleanup_residue": 0,
            "rollback_verified": True,
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        },
        "lifecycle": {
            "lifecycle_gate": "PASS",
            "consent_race_safety": "PASS",
            "fact_residue": 0,
            "consent_residue": 0,
            "cleanup_residue": 0,
            "production_observation": "NOT_RUN",
        },
        "restore": {
            "backup_restore_tombstone_replay": "PASS",
            "restore_cycles": 3,
            "fault_boundaries_exercised": 6,
            "fault_reclaims_completed": 6,
            "restored_private_data_residue": 0,
            "public_knowledge_unchanged": True,
            "production_observation": "NOT_RUN",
        },
        "status": {
            "automatic_stop": {"triggered": False, "gate_codes": [], "expansion_allowed": True},
            "budget": {"data_complete": True, "sample_sufficient": True},
            "write": {"sample_sufficient": True},
            "read": {"sample_sufficient": True, "prompt_isolation_violation_count": 0},
            "hold_codes": [],
            "configuration_changed": False,
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        },
        "security": {
            "review_status": "PASS",
            "artifact_violations": 0,
            "artifacts_audited": 9,
            "hard_stop_count": 0,
            "knowledge_firewall_violations": 0,
            "protected_taxonomy_hits": 0,
            "public_knowledge_unchanged": True,
            "production_observation": "NOT_RUN",
        },
        "repository": {
            "safe_defaults": True,
            "consume_rejected": True,
            "rc_revision_is_ancestor": True,
        },
    }


def test_all_operational_gates_produce_exact_success_lines_and_safe_evidence():
    bundle = accepted_bundle()

    lines = evaluate_operational_shadow(bundle)
    evidence = build_acceptance_evidence(bundle)

    assert lines == SUCCESS_LINES
    assert lines == (
        "MEMORY_SHADOW_RC=REPRODUCIBLE",
        "BUDGET_SHADOW_STAGING=PASS",
        "PRINCIPAL_WRITE_SHADOW_STAGING=PASS",
        "PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS",
        "CONSENT_DELETION_RESTORE_DRILL=PASS",
        "PRODUCTION_SHADOW_APPROVAL_REQUIRED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert evidence["accepted"] is True
    assert evidence["validated_rc_revision"] == "a982b1f"
    assert evidence["validation_counts"]["full_python_passed"] == 1500
    assert evidence["observation_profile"] == "B"
    assert evidence["cleanup"]["private_data_residue"] == 0
    validate_acceptance_artifact(evidence)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value["regression"]["full_python"].update({"passed": False, "failed": 1}), "FULL_PYTHON_REGRESSION_FAILED"),
        (lambda value: value["budget"].update({"mandatory_current_content_losses": 1}), "BUDGET_MANDATORY_CONTENT_LOSS"),
        (lambda value: value["write"]["hard_invariants"].update({"automatic_active": 1}), "PRINCIPAL_WRITE_HARD_INVARIANT"),
        (lambda value: value["read"].update({"provider_isolation": "BLOCKED"}), "PRINCIPAL_READ_ZERO_INJECTION_FAILED"),
        (lambda value: value["restore"].update({"restored_private_data_residue": 1}), "RESTORE_PRIVATE_DATA_RESIDUE"),
        (lambda value: value["security"].update({"review_status": "BLOCKED"}), "SECURITY_REVIEW_FAILED"),
        (lambda value: value["repository"].update({"consume_rejected": False}), "CONSUME_NOT_REJECTED"),
    ],
)
def test_any_failed_gate_blocks_without_ready_output(mutator, code):
    bundle = accepted_bundle()
    mutator(bundle)

    with pytest.raises(AcceptanceBlocked) as raised:
        evaluate_operational_shadow(bundle)

    assert code in raised.value.codes
    output = format_blocked_output(raised.value.codes)
    assert output[0] == "MEMORY_OPERATIONAL_SHADOW=BLOCKED"
    assert f"GATE={code}" in output
    assert output[-2:] == (
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert not any("READY" in line or "=PASS" in line for line in output)


def test_committed_default_config_is_disabled_and_consume_is_rejected():
    config = load_effective_memory_config({})
    assert config.budget.mode == "disabled"
    assert config.compression.mode == "disabled"
    assert config.long_term.mode == "disabled"
    assert config.long_term.write_shadow_enabled is False
    assert config.long_term.read_shadow_enabled is False
    with pytest.raises(ValueError, match="consume is not supported"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})


def test_acceptance_artifact_rejects_private_keys_or_non_not_run_production():
    evidence = build_acceptance_evidence(accepted_bundle())
    unsafe = deepcopy(evidence)
    unsafe["principal_id"] = "private"
    with pytest.raises(RuntimeError, match="private"):
        validate_acceptance_artifact(unsafe)

    unsafe = deepcopy(evidence)
    unsafe["production_observation"] = "PASS"
    with pytest.raises(RuntimeError, match="production"):
        validate_acceptance_artifact(unsafe)
