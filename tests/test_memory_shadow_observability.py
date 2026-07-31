from copy import deepcopy
import json

import pytest

from app.ports.memory_shadow_observability import (
    MemoryShadowEvidenceSource,
    MemoryShadowStatusBuilder,
)
from app.services.memory_shadow_observability import (
    MemoryShadowObservabilityService,
    validate_status_artifact,
)
from scripts.memory_shadow_status import FileMemoryShadowEvidenceSource


def evidence_bundle():
    return {
        "budget": {
            "followup_sample_count": 300,
            "language_sample_counts": {"en": 100, "mixed": 100, "zh_hans": 100},
            "estimator_error_direction": {"over": 300},
            "known_over_budget_provider_calls": 0,
            "mandatory_current_content_losses": 0,
            "would_select_count": 3360,
            "would_drop_count": 1800,
            "fallback_count": 300,
            "baseline_error_rate": 0.0,
            "followup_error_rate": 0.0,
            "baseline_p95_latency_ms": 500.0,
            "followup_p95_latency_ms": 533.252,
            "data_complete": True,
            "unavailable_bucket_count": 0,
            "privacy_audit_hits": 0,
            "budget_config_conflict": False,
            "cleanup_residue": 0,
        },
        "write": {
            "sample_count": 300,
            "proposal_created_count": 300,
            "deduplicated_replay_count": 9,
            "fault_matrix": {
                "candidate_rejected": 3,
                "consent_unavailable": 1,
                "extractor_failure_contained": 1,
                "identity_changed": 1,
                "identity_unavailable": 1,
                "source_unavailable": 1,
                "source_version_changed": 1,
            },
            "hard_invariants": {
                "automatic_active": 0,
                "automatic_user_confirmed": 0,
                "cross_principal_write": 0,
                "privacy_artifact_hit": 0,
                "public_knowledge_write": 0,
                "without_consent_proposal": 0,
            },
            "cleanup_residue": 0,
        },
        "quality": {
            "reviewed_count": 300,
            "label_counts": {"correct": 285, "unsupported": 3},
            "privacy_sensitive_count": 0,
            "stale_source_accepted_count": 0,
            "quality_gate": "PASS",
        },
        "lifecycle": {
            "confirmed_count": 1,
            "superseded_count": 1,
            "rejected_count": 1,
            "selected_after_revoke": 0,
            "fact_residue": 0,
            "consent_residue": 0,
            "race_matrix": {"unsafe_race_write_count": 0},
        },
        "read": {
            "sample_count": 300,
            "scenario_counts": {
                "conflict": 38,
                "deleted_source": 38,
                "expired": 37,
                "fact_cap": 37,
                "revoked_consent": 38,
            },
            "source_fact_count": 337,
            "would_select_count": 149,
            "conflict_count": 38,
            "hard_invariants": {
                "provider_context_mutation": 0,
                "provider_request_mutation": 0,
                "question_score_report_mutation": 0,
                "cross_principal_selected": 0,
                "consent_revoked_selected": 0,
                "revoked_expired_deleted_selected": 0,
                "unconfirmed_selected": 0,
                "fact_token_limit_violation": 0,
            },
            "read_shadow_p95_latency_ms": 564.684,
            "baseline_p95_latency_ms": 500.0,
            "latency_regression_ratio": 0.129368,
            "provider_calls": 0,
            "cleanup_residue": 0,
        },
    }


def test_status_builds_all_three_aggregate_panels_and_passes_stop_gate():
    result = MemoryShadowObservabilityService().build_status(evidence_bundle())

    assert result["schema_version"] == "memory-shadow-status-v1"
    assert result["status_only"] is True
    assert result["budget"]["sample_sufficient"] is True
    assert result["budget"]["would_select_count"] == 3360
    assert result["write"]["identity_unavailable_count"] == 1
    assert result["write"]["taxonomy_rejection_count"] == 1
    assert result["write"]["reviewed_count"] == 300
    assert result["read"]["would_select_count"] == 149
    assert result["read"]["prompt_isolation_violation_count"] == 0
    assert result["automatic_stop"]["triggered"] is False
    assert result["automatic_stop"]["gate_codes"] == []
    assert result["automatic_stop"]["expansion_allowed"] is True
    assert result["automatic_stop"]["deterministic_path_available"] is True
    assert result["configuration_changed"] is False
    assert result["long_term_memory_consumption"] == "BLOCKED"
    validate_status_artifact(result)


@pytest.mark.parametrize(
    ("mutator", "expected_code", "privacy_notification"),
    [
        (
            lambda value: value["budget"].update(
                {"mandatory_current_content_losses": 1}
            ),
            "BUDGET_MANDATORY_CONTENT_LOSS",
            False,
        ),
        (
            lambda value: value["write"]["hard_invariants"].update(
                {"cross_principal_write": 1}
            ),
            "PRINCIPAL_WRITE_PRIVACY_SCOPE_VIOLATION",
            True,
        ),
        (
            lambda value: value["read"]["hard_invariants"].update(
                {"provider_context_mutation": 1}
            ),
            "PRINCIPAL_READ_PROMPT_ISOLATION_VIOLATION",
            True,
        ),
    ],
)
def test_hard_stop_is_fail_closed_and_preserves_deterministic_path(
    mutator, expected_code, privacy_notification
):
    evidence = evidence_bundle()
    mutator(evidence)

    result = MemoryShadowObservabilityService().build_status(evidence)

    assert result["automatic_stop"]["triggered"] is True
    assert expected_code in result["automatic_stop"]["gate_codes"]
    assert result["automatic_stop"]["expansion_allowed"] is False
    assert result["automatic_stop"]["new_shadow_worker_leasing_allowed"] is False
    assert result["automatic_stop"]["target_modes"] == {
        "budget": "disabled",
        "principal_read": "disabled",
        "principal_write": "disabled",
    }
    assert result["automatic_stop"]["operator_notification_required"] is True
    assert (
        result["automatic_stop"]["privacy_notification_required"]
        is privacy_notification
    )
    assert result["automatic_stop"]["deterministic_path_available"] is True


def test_incomplete_or_low_sample_data_cannot_expand_but_is_not_a_hard_stop():
    evidence = evidence_bundle()
    evidence["budget"]["followup_sample_count"] = 20
    evidence["budget"]["language_sample_counts"] = {"en": 8, "mixed": 6, "zh_hans": 6}

    result = MemoryShadowObservabilityService().build_status(evidence)

    assert result["budget"]["sample_sufficient"] is False
    assert result["automatic_stop"]["triggered"] is False
    assert result["automatic_stop"]["expansion_allowed"] is False
    assert "BUDGET_SAMPLE_INSUFFICIENT" in result["hold_codes"]


def test_private_or_high_cardinality_evidence_is_rejected():
    evidence = evidence_bundle()
    evidence["write"]["principal_id"] = "private"

    with pytest.raises(ValueError, match="high-cardinality"):
        MemoryShadowObservabilityService().build_status(evidence)


def test_status_validator_rejects_mutating_or_private_status_payload():
    result = MemoryShadowObservabilityService().build_status(evidence_bundle())
    unsafe = deepcopy(result)
    unsafe["configuration_changed"] = True
    with pytest.raises(RuntimeError, match="configuration"):
        validate_status_artifact(unsafe)

    unsafe = deepcopy(result)
    unsafe["session_id"] = "private"
    with pytest.raises(RuntimeError, match="high-cardinality"):
        validate_status_artifact(unsafe)


def test_observability_ports_and_file_source_are_read_only(tmp_path):
    paths = {}
    for stage, value in evidence_bundle().items():
        path = tmp_path / f"{stage}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[stage] = path
    source = FileMemoryShadowEvidenceSource(paths)
    builder = MemoryShadowObservabilityService()

    assert isinstance(source, MemoryShadowEvidenceSource)
    assert isinstance(builder, MemoryShadowStatusBuilder)
    assert builder.build_status(source.load())["configuration_changed"] is False
