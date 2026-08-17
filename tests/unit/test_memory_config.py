from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.runtime.config.memory import (
    SelectionMemoryConfig,
    load_effective_memory_config,
    memory_readiness_payload,
)


def test_adaptive_context_compression_defaults_are_frozen_and_behavior_preserving():
    config = load_effective_memory_config({})

    assert config.compression.task_intent_enabled is False
    assert config.compression.status_projection_enabled is False
    assert config.compression.provider_circuit_threshold == 3
    assert config.compression.provider_circuit_cooldown_seconds == 300
    assert config.compression.validation_quarantine_threshold == 2
    assert config.compression.validation_quarantine_cooldown_seconds == 3_600
    assert config.compression.failure_state_lease_seconds == 60
    assert config.selection.exact_recent_questions == 1
    assert config.selection.max_memory_units == 4
    assert config.selection.max_memory_tokens == 2_500
    assert config.selection.eligibility_utilization_basis_points == 8_000
    assert config.selection.exact_deduplication_mode == "disabled"
    assert config.selection.dynamic_target_floor_tokens == 256
    assert config.selection.dynamic_target_source_ratio_basis_points == 2_500
    assert config.selection.dynamic_target_allowed_tokens == (
        256,
        512,
        1_024,
        1_536,
        2_000,
    )
    assert isinstance(config.selection.dynamic_target_allowed_tokens, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        config.selection.dynamic_target_allowed_tokens = (256, 512)


def test_adaptive_context_compression_fields_load_from_new_only_environment():
    config = load_effective_memory_config(
        {
            "MEMORY_COMPRESSION_TASK_INTENT_ENABLED": "true",
            "MEMORY_COMPRESSION_STATUS_PROJECTION_ENABLED": "true",
            "MEMORY_COMPRESSION_PROVIDER_CIRCUIT_THRESHOLD": "4",
            "MEMORY_COMPRESSION_PROVIDER_CIRCUIT_COOLDOWN_SECONDS": "601",
            "MEMORY_COMPRESSION_VALIDATION_QUARANTINE_THRESHOLD": "5",
            "MEMORY_COMPRESSION_VALIDATION_QUARANTINE_COOLDOWN_SECONDS": "602",
            "MEMORY_COMPRESSION_FAILURE_STATE_LEASE_SECONDS": "600",
            "MEMORY_SELECTION_EXACT_RECENT_QUESTIONS": "2",
            "MEMORY_SELECTION_MAX_MEMORY_UNITS": "3",
            "MEMORY_SELECTION_MAX_MEMORY_TOKENS": "2400",
            "MEMORY_SELECTION_ELIGIBILITY_UTILIZATION_BASIS_POINTS": "7500",
            "MEMORY_SELECTION_EXACT_DEDUPLICATION_MODE": "ENFORCE",
            "MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS": "300",
            "MEMORY_SELECTION_DYNAMIC_TARGET_SOURCE_RATIO_BASIS_POINTS": "3333",
            "MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": (
                "300, 600, 1200, 2000"
            ),
        }
    )

    assert config.compression.model_dump() | config.selection.model_dump() == {
        "mode": "disabled",
        "interview_question_memory": False,
        "evidence": False,
        "prep": False,
        "review": False,
        "task_intent_enabled": True,
        "status_projection_enabled": True,
        "provider_circuit_threshold": 4,
        "provider_circuit_cooldown_seconds": 601,
        "validation_quarantine_threshold": 5,
        "validation_quarantine_cooldown_seconds": 602,
        "failure_state_lease_seconds": 600,
        "exact_recent_questions": 2,
        "max_memory_units": 3,
        "max_memory_tokens": 2_400,
        "eligibility_utilization_basis_points": 7_500,
        "exact_deduplication_mode": "enforce",
        "dynamic_target_floor_tokens": 300,
        "dynamic_target_source_ratio_basis_points": 3_333,
        "dynamic_target_allowed_tokens": (300, 600, 1_200, 2_000),
    }
    assert config.legacy_environment_used is False


def test_adaptive_context_compression_does_not_introduce_legacy_aliases():
    config = load_effective_memory_config(
        {
            "CONTEXT_COMPRESSION_TASK_INTENT_ENABLED": "true",
            "CONTEXT_SELECTION_MAX_MEMORY_UNITS": "99",
        }
    )

    assert config.compression.task_intent_enabled is False
    assert config.selection.max_memory_units == 4
    assert config.legacy_environment_used is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEMORY_COMPRESSION_PROVIDER_CIRCUIT_THRESHOLD", "0"),
        ("MEMORY_COMPRESSION_PROVIDER_CIRCUIT_THRESHOLD", "101"),
        ("MEMORY_COMPRESSION_VALIDATION_QUARANTINE_THRESHOLD", "0"),
        ("MEMORY_COMPRESSION_VALIDATION_QUARANTINE_THRESHOLD", "101"),
        ("MEMORY_COMPRESSION_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "0"),
        ("MEMORY_COMPRESSION_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "86401"),
        ("MEMORY_COMPRESSION_VALIDATION_QUARANTINE_COOLDOWN_SECONDS", "0"),
        ("MEMORY_COMPRESSION_VALIDATION_QUARANTINE_COOLDOWN_SECONDS", "86401"),
        ("MEMORY_COMPRESSION_FAILURE_STATE_LEASE_SECONDS", "0"),
        ("MEMORY_COMPRESSION_FAILURE_STATE_LEASE_SECONDS", "86401"),
        ("MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS", "2001"),
    ],
)
def test_adaptive_context_compression_bounds_fail_during_loading(name, value):
    with pytest.raises(ValueError):
        load_effective_memory_config({name: value})


@pytest.mark.parametrize(
    "environment",
    [
        {
            "MEMORY_COMPRESSION_FAILURE_STATE_LEASE_SECONDS": "300",
            "MEMORY_COMPRESSION_PROVIDER_CIRCUIT_COOLDOWN_SECONDS": "300",
        },
        {
            "MEMORY_COMPRESSION_FAILURE_STATE_LEASE_SECONDS": "3600",
            "MEMORY_COMPRESSION_VALIDATION_QUARANTINE_COOLDOWN_SECONDS": "3600",
        },
    ],
)
def test_failure_state_lease_must_be_shorter_than_both_cooldowns(environment):
    with pytest.raises(ValueError, match="lease must be shorter"):
        load_effective_memory_config(environment)


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        (
            {"MEMORY_SELECTION_EXACT_DEDUPLICATION_MODE": "enabled"},
            "disabled, shadow, or enforce",
        ),
        (
            {"MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": "256,256,512"},
            "duplicates",
        ),
        (
            {
                "MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS": "512",
                "MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": "512,256",
            },
            "strictly increasing",
        ),
        (
            {
                "MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS": "300",
                "MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": "256,512",
            },
            "configured floor",
        ),
        (
            {"MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": "256,2001"},
            "hard cap",
        ),
    ],
)
def test_deduplication_mode_and_dynamic_target_tiers_fail_closed(
    environment,
    message,
):
    with pytest.raises(ValueError, match=message):
        load_effective_memory_config(environment)


def test_dynamic_target_tiers_cannot_be_empty_even_in_direct_frozen_model():
    with pytest.raises(ValueError, match="must not be empty"):
        SelectionMemoryConfig(dynamic_target_allowed_tokens=())


def test_new_structured_value_is_used_without_legacy_flag():
    config = load_effective_memory_config(
        {"MEMORY_INTERVIEW_GRAPH_ROLLOUT_PERCENT": "5"}
    )

    assert config.interview_graph.rollout_percent == 5
    assert config.legacy_environment_used is False


def test_legacy_value_is_adapted_and_marked():
    config = load_effective_memory_config(
        {"INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT": "5"}
    )

    assert config.interview_graph.rollout_percent == 5
    assert config.legacy_environment_used is True


def test_equal_new_and_legacy_values_are_accepted_after_normalization():
    config = load_effective_memory_config(
        {
            "MEMORY_INTERVIEW_GRAPH_RUNTIME_ENABLED": "TRUE",
            "INTERVIEW_LANGGRAPH_RUNTIME_ENABLED": "true",
        }
    )

    assert config.interview_graph.runtime_enabled is True
    assert config.legacy_environment_used is True


def test_conflicting_new_and_legacy_values_fail_closed():
    with pytest.raises(ValueError, match="conflicting memory configuration"):
        load_effective_memory_config(
            {
                "MEMORY_INTERVIEW_GRAPH_ROLLOUT_PERCENT": "1",
                "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT": "5",
            }
        )


def test_structured_mode_conflicting_with_legacy_gates_fails_closed():
    with pytest.raises(ValueError, match="MEMORY_COMPRESSION_MODE"):
        load_effective_memory_config(
            {
                "MEMORY_COMPRESSION_MODE": "disabled",
                "CONTEXT_COMPRESSION_SHADOW_ENABLED": "true",
            }
        )


def test_rollout_requires_enabled_runtime():
    with pytest.raises(ValueError, match="rollout requires runtime enabled"):
        load_effective_memory_config(
            {
                "MEMORY_INTERVIEW_GRAPH_RUNTIME_ENABLED": "false",
                "MEMORY_INTERVIEW_GRAPH_ROLLOUT_PERCENT": "1",
            }
        )


def test_v2_rollout_requires_interview_budget_enforcement():
    with pytest.raises(ValueError, match="requires interview budget enforcement"):
        load_effective_memory_config(
            {
                "MEMORY_INTERVIEW_GRAPH_VERSION": "langgraph-v2",
                "MEMORY_INTERVIEW_GRAPH_ROLLOUT_PERCENT": "1",
            }
        )


def test_compression_consumption_requires_budget_and_artifact_store():
    with pytest.raises(ValueError, match="requires interview budget enforcement"):
        load_effective_memory_config(
            {"MEMORY_COMPRESSION_MODE": "consume"}
        )

    with pytest.raises(ValueError, match="requires an artifact store"):
        load_effective_memory_config(
            {
                "MEMORY_COMPRESSION_MODE": "consume",
                "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "true",
                "MEMORY_ARTIFACT_STORE_AVAILABLE": "false",
            }
        )


def test_evidence_requires_interview_or_review_compression():
    with pytest.raises(ValueError, match="evidence consumption requires"):
        load_effective_memory_config(
            {"MEMORY_COMPRESSION_EVIDENCE": "true"}
        )


def test_unknown_model_requires_explicit_context_window():
    with pytest.raises(ValueError, match="unknown model requires"):
        load_effective_memory_config({"OPENAI_MODEL": "private-proxy-model"})


def test_readiness_payload_contains_only_safe_effective_modes():
    config = load_effective_memory_config({})

    payload = memory_readiness_payload(config)

    assert payload == {
        "schema_version": "memory-runtime-config-v1",
        "configuration_valid": True,
        "budget_mode": "disabled",
        "compression_mode": "disabled",
        "task_intent_enabled": False,
        "status_projection_enabled": False,
        "provider_circuit_threshold": 3,
        "provider_circuit_cooldown_seconds": 300,
        "validation_quarantine_threshold": 2,
        "validation_quarantine_cooldown_seconds": 3_600,
        "failure_state_lease_seconds": 60,
        "exact_recent_questions": 1,
        "max_memory_units": 4,
        "max_memory_tokens": 2_500,
        "eligibility_utilization_basis_points": 8_000,
        "exact_deduplication_mode": "disabled",
        "dynamic_target_floor_tokens": 256,
        "dynamic_target_source_ratio_basis_points": 2_500,
        "dynamic_target_allowed_tokens": [256, 512, 1_024, 1_536, 2_000],
        "long_term_mode": "local_consume",
        "local_principal_enabled": True,
        "local_consumption_enabled": True,
        "interview_graph_version": "langgraph-v1",
        "interview_graph_rollout_percent": 0,
        "legacy_environment_used": False,
        "consumption_ready": True,
        "reason": None,
    }
    assert set(payload) == {
        "schema_version",
        "configuration_valid",
        "budget_mode",
        "compression_mode",
        "task_intent_enabled",
        "status_projection_enabled",
        "provider_circuit_threshold",
        "provider_circuit_cooldown_seconds",
        "validation_quarantine_threshold",
        "validation_quarantine_cooldown_seconds",
        "failure_state_lease_seconds",
        "exact_recent_questions",
        "max_memory_units",
        "max_memory_tokens",
        "eligibility_utilization_basis_points",
        "exact_deduplication_mode",
        "dynamic_target_floor_tokens",
        "dynamic_target_source_ratio_basis_points",
        "dynamic_target_allowed_tokens",
        "long_term_mode",
        "local_principal_enabled",
        "local_consumption_enabled",
        "interview_graph_version",
        "interview_graph_rollout_percent",
        "legacy_environment_used",
        "consumption_ready",
        "reason",
    }
    assert "base_url" not in payload


def test_operator_tombstone_ledger_path_is_private_config_not_readiness_data(
    tmp_path,
):
    ledger_path = (tmp_path / "private" / "principal-memory-tombstones.jsonl").resolve()
    config = load_effective_memory_config(
        {
            "MEMORY_PRINCIPAL_TOMBSTONE_LEDGER_PATH": str(ledger_path)
        }
    )

    assert config.long_term.operator_tombstone_ledger_path == str(ledger_path)
    assert "tombstone_ledger" not in repr(memory_readiness_payload(config))
    with pytest.raises(ValueError, match="absolute JSONL"):
        load_effective_memory_config(
            {"MEMORY_PRINCIPAL_TOMBSTONE_LEDGER_PATH": "relative.jsonl"}
        )


def test_question_memory_consumption_readiness_fails_when_required_coverage_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.knowledge_profile.load_active_knowledge_covered_tags",
        lambda: {"python"},
    )
    config = load_effective_memory_config(
        {
            "MEMORY_BUDGET_MODE": "enforce",
            "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "true",
            "MEMORY_COMPRESSION_MODE": "consume",
            "MEMORY_COMPRESSION_INTERVIEW_QUESTION_MEMORY": "true",
        }
    )

    payload = memory_readiness_payload(config)

    assert payload["configuration_valid"] is False
    assert payload["consumption_ready"] is False
    assert payload["reason"] == "knowledge_coverage_unavailable"


def test_question_memory_consumption_readiness_accepts_reviewed_p1_manifest():
    config = load_effective_memory_config(
        {
            "MEMORY_BUDGET_MODE": "enforce",
            "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "true",
            "MEMORY_COMPRESSION_MODE": "consume",
            "MEMORY_COMPRESSION_INTERVIEW_QUESTION_MEMORY": "true",
        }
    )

    payload = memory_readiness_payload(config)

    assert payload["configuration_valid"] is True
    assert payload["consumption_ready"] is True
    assert payload["reason"] is None


def test_long_term_memory_defaults_to_loopback_local_consume_and_legacy_consume_fails():
    config = load_effective_memory_config({})
    assert config.long_term.mode == "local_consume"
    assert config.long_term.write_shadow_enabled is True
    assert config.long_term.read_shadow_enabled is True
    assert config.long_term.trusted_local_api_enabled is True
    assert config.long_term.local_principal_enabled is True
    assert config.long_term.local_principal_id == "local-owner"
    assert config.long_term.local_consumption_enabled is True
    assert config.long_term.proposal_retention_days == 7
    assert config.long_term.active_fact_default_days == 180

    with pytest.raises(ValueError, match="cannot be downgraded"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})

    disabled = load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "disabled"})
    assert disabled.long_term.mode == "disabled"
    assert disabled.long_term.local_principal_enabled is False
    assert disabled.long_term.trusted_local_api_enabled is False
    assert disabled.long_term.local_consumption_enabled is False


def test_local_consume_requires_every_static_local_gate():
    complete = {
        "MEMORY_LONG_TERM_MODE": "local_consume",
        "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
        "MEMORY_LOCAL_PRINCIPAL_ID": "local-owner",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
    }

    config = load_effective_memory_config(complete)

    assert config.long_term.mode == "local_consume"
    assert config.long_term.local_principal_enabled is True
    assert config.long_term.local_consumption_enabled is True

    required = {
        "MEMORY_LOCAL_PRINCIPAL_ENABLED": "local Principal gate",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "trusted-local API gate",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "write and read shadow gates",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "write and read shadow gates",
        "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "local consumption gate",
    }
    for name, error in required.items():
        invalid = dict(complete)
        invalid[name] = "false"
        with pytest.raises(ValueError, match=error):
            load_effective_memory_config(invalid)

    wrong_scope = dict(complete)
    wrong_scope["MEMORY_PRIVACY_DEPLOYMENT_ID"] = "hosted-production"
    with pytest.raises(ValueError, match="single-tenant-local deployment scope"):
        load_effective_memory_config(wrong_scope)


def test_local_gates_cannot_be_reinterpreted_outside_local_scope_or_mode():
    with pytest.raises(ValueError, match="single-tenant-local deployment scope"):
        load_effective_memory_config(
            {
                "MEMORY_LONG_TERM_MODE": "read_shadow",
                "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
                "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
                "MEMORY_PRIVACY_DEPLOYMENT_ID": "hosted-production",
            }
        )

    with pytest.raises(ValueError, match="requires local_consume mode"):
        load_effective_memory_config(
            {"MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true"}
        )


def test_local_principal_configuration_rejects_inference_shaped_identifiers():
    for value in (
        "person@example.com",
        "candidate phone",
        "principal/../../other",
        "",
    ):
        with pytest.raises(ValueError, match="stable identifier"):
            load_effective_memory_config(
                {
                    "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
                    "MEMORY_LOCAL_PRINCIPAL_ID": value,
                }
            )


def test_readiness_snapshot_exposes_only_local_gate_state():
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "read_shadow",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
            "MEMORY_LOCAL_PRINCIPAL_ID": "local-owner",
        }
    )

    payload = memory_readiness_payload(config)

    assert payload["local_principal_enabled"] is True
    assert payload["local_consumption_enabled"] is False
    assert "local_principal_id" not in payload


def test_long_term_shadow_modes_require_explicit_matching_gates():
    with pytest.raises(ValueError, match="explicit write gate"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "write_shadow"})
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "read_shadow",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        }
    )
    assert config.long_term.mode == "read_shadow"
    assert config.long_term.write_shadow_enabled is False


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        (
            {
                "MEMORY_LONG_TERM_MODE": "read_shadow",
                "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
                "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            },
            "read_shadow mode forbids the write gate",
        ),
        (
            {
                "MEMORY_LONG_TERM_MODE": "write_shadow",
                "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
                "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            },
            "write_shadow mode forbids the read gate",
        ),
        (
            {
                "MEMORY_LONG_TERM_MODE": "read_shadow",
                "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
                "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
            },
            "local consumption gate requires local_consume mode",
        ),
        (
            {"MEMORY_LOCAL_PRINCIPAL_ENABLED": "true"},
            "disabled long-term memory forbids the local Principal gate",
        ),
        (
            {
                "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            },
            "disabled long-term memory forbids the trusted-local API gate",
        ),
        (
            {
                "MEMORY_LONG_TERM_MODE": "read_shadow",
                "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
                "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            },
            "read_shadow mode forbids the trusted-local API gate",
        ),
        (
            {
                "MEMORY_LONG_TERM_MODE": "write_shadow",
                "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
                "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            },
            "write_shadow mode forbids the trusted-local API gate",
        ),
    ],
)
def test_long_term_mode_is_authoritative_over_capability_gates(
    environment,
    message,
):
    with pytest.raises(ValueError, match=message):
        load_effective_memory_config(environment)
