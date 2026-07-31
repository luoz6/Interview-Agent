import pytest

from app.services.memory_config import (
    load_effective_memory_config,
    memory_readiness_payload,
)


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
        "long_term_mode": "disabled",
        "interview_graph_version": "langgraph-v1",
        "interview_graph_rollout_percent": 0,
        "legacy_environment_used": False,
        "consumption_ready": True,
        "reason": None,
    }
    assert "base_url" not in payload


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


def test_long_term_memory_defaults_disabled_and_consume_fails_closed():
    config = load_effective_memory_config({})
    assert config.long_term.mode == "disabled"
    assert config.long_term.write_shadow_enabled is False
    assert config.long_term.read_shadow_enabled is False
    assert config.long_term.trusted_local_api_enabled is False

    with pytest.raises(ValueError, match="cannot be downgraded"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})


def test_long_term_shadow_modes_require_explicit_matching_gates():
    with pytest.raises(ValueError, match="explicit write gate"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "write_shadow"})
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "read_shadow",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        }
    )
    assert config.long_term.mode == "read_shadow"
