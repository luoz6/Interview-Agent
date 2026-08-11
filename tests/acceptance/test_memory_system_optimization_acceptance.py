from __future__ import annotations

import pytest

from scripts import memory_system_optimization_acceptance as acceptance


_ADAPTIVE_PLAN_FIXTURE = """# Adaptive plan

Pinned Spec v1.1.2-draft.

Requirements: MEM-CTX-PLAN-001
"""
_NORMATIVE = (
    "- `MEM-CTX-PLAN-001`: The plan MUST be traceable.\n"
)
_VERIFICATION = (
    "- Verification `MEM-CTX-PLAN-001`: `tests/test_acceptance.py`.\n"
)
_EXPECTED_FIXED_TESTS = (
    "tests/unit/test_memory_config.py",
    "tests/unit/test_agent_runtime_composition.py",
    "tests/unit/test_context_budget.py",
    "tests/unit/test_context_selection.py",
    "tests/unit/test_context_source_identity.py",
    "tests/unit/test_context_compression_eligibility.py",
    "tests/unit/test_context_compressor.py",
    "tests/contracts/test_context_compression_validation.py",
    "tests/unit/test_context_compression_runner.py",
    "tests/contracts/test_context_artifacts.py",
    "tests/contracts/test_context_artifact_contracts.py",
    "tests/integration/postgres/test_context_artifact_store_postgres.py",
    "tests/unit/test_interview_context_artifacts.py",
    "tests/unit/test_evidence_context_artifacts.py",
    "tests/unit/test_question_memory.py",
    "tests/unit/test_question_memory_retrieval.py",
    "tests/unit/test_question_memory_recovery.py",
    "tests/unit/test_interview_status_projection.py",
    "tests/unit/test_context_compression_failure_containment.py",
    "tests/integration/postgres/test_context_compression_failure_store_postgres.py",
    "tests/acceptance/test_context_compression_shadow_acceptance.py",
    "tests/unit/test_durable_interview_state.py",
    "tests/unit/test_durable_interview_graph.py",
    "tests/unit/test_session_deletion_worker.py",
    "tests/unit/test_memory_metrics.py",
    "tests/acceptance/test_memory_system_optimization_acceptance.py",
    "tests/acceptance/test_context_compression_repository_acceptance.py",
)
_EXPECTED_SCENARIOS = {
    "all_gates_disabled",
    "short_shadow_context",
    "follow_up_6687_of_8360_below_threshold",
    "rounded_8000_bp_cross_product_below_threshold",
    "pre_loss_80_percent_shadow",
    "dedup_shadow",
    "business_eligible_shadow_post_dedup_below_threshold",
    "dedup_enforce",
    "valid_artifact_consume",
    "completed_artifact_reuse",
    "invalid_compression_fallback",
    "provider_circuit_open",
    "validation_source_quarantined",
    "same_text_distinct_question_identities",
    "oversized_mandatory_bounded_raw_set",
    "identity_v0_reload",
    "identity_v1_reload",
    "quarantined_source_owner_isolation",
    "concurrent_half_open_probes",
    "parent_lease_loss",
    "digest_conflict",
    "v1_checkpoint_recovery",
    "v2_compatibility_checkpoint_recovery",
    "session_deletion",
}


def test_adaptive_context_plan_requirements_are_exact_and_traceable():
    acceptance.verify_adaptive_context_traceability()


def test_adaptive_context_plan_has_one_verification_mapping_per_requirement():
    plan_ids, normative_ids, verification_ids = (
        acceptance.adaptive_context_traceability_sets()
    )

    assert len(plan_ids) == 27
    assert plan_ids == normative_ids == verification_ids


def test_adaptive_traceability_parser_accepts_crlf_and_unicode_colons():
    spec_text = (
        _NORMATIVE.replace("`:", "`：")
        + _VERIFICATION.replace("`:", "`：")
    ).replace("\n", "\r\n")

    plan_ids, normative_ids, verification_ids = (
        acceptance.verify_adaptive_context_traceability_texts(
            plan_text=_ADAPTIVE_PLAN_FIXTURE.replace("\n", "\r\n"),
            spec_text=spec_text,
        )
    )

    assert plan_ids == normative_ids == verification_ids == {
        "MEM-CTX-PLAN-001"
    }


@pytest.mark.parametrize(
    ("spec_text", "expected_message"),
    (
        (
            _VERIFICATION,
            "adaptive Plan references missing normative Spec IDs: "
            "MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + _NORMATIVE.replace("`:", "`：")
            + _VERIFICATION,
            "duplicate adaptive normative Spec IDs: MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + "- `MEM-CTX-EXTRA-999`: Unplanned requirement.\n"
            + _VERIFICATION,
            "adaptive Spec has unreferenced normative IDs: MEM-CTX-EXTRA-999",
        ),
    ),
)
def test_adaptive_normative_parser_rejects_exact_traceability_errors(
    spec_text,
    expected_message,
):
    with pytest.raises(RuntimeError) as exc_info:
        acceptance.verify_adaptive_context_traceability_texts(
            plan_text=_ADAPTIVE_PLAN_FIXTURE,
            spec_text=spec_text,
        )

    assert str(exc_info.value) == expected_message


@pytest.mark.parametrize(
    ("spec_text", "expected_message"),
    (
        (
            _NORMATIVE,
            "adaptive requirements missing verification mappings: "
            "MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + _VERIFICATION
            + _VERIFICATION.replace("`:", "`："),
            "duplicate adaptive verification mappings: MEM-CTX-PLAN-001",
        ),
        (
            _NORMATIVE
            + _VERIFICATION
            + "- Verification `MEM-CTX-EXTRA-999`: "
            "`tests/test_extra.py`.\n",
            "adaptive verification mappings reference unplanned IDs: "
            "MEM-CTX-EXTRA-999",
        ),
    ),
)
def test_adaptive_verification_parser_rejects_exact_mapping_errors(
    spec_text,
    expected_message,
):
    with pytest.raises(RuntimeError) as exc_info:
        acceptance.verify_adaptive_context_traceability_texts(
            plan_text=_ADAPTIVE_PLAN_FIXTURE,
            spec_text=spec_text,
        )

    assert str(exc_info.value) == expected_message


def test_historical_plan_traceability_still_passes():
    acceptance.verify_traceability()


def test_historical_acceptance_quick_gate_preserves_master_status(capsys):
    assert acceptance.main(["--skip-tests"]) == 0
    assert capsys.readouterr().out == (
        "READY_FOR_MEMORY_SYSTEM_SHADOW\n"
        "PRODUCTION_OBSERVATION=NOT_RUN\n"
    )


def test_repository_acceptance_keeps_rollout_and_consumption_defaults_safe():
    acceptance.verify_safe_defaults()


def test_repository_acceptance_uses_the_exact_task_10_fixed_suite():
    assert acceptance.ADAPTIVE_FOCUSED_TESTS == _EXPECTED_FIXED_TESTS


def test_task_0_to_10_declared_tests_are_covered_without_exemptions():
    assert acceptance.REVIEWED_TEST_EXEMPTIONS == {}
    acceptance.verify_acceptance_manifest()


def test_task_test_coverage_fails_closed_when_a_declared_test_is_omitted():
    reduced_suite = tuple(
        test_module
        for test_module in acceptance.ADAPTIVE_FOCUSED_TESTS
        if test_module != "tests/unit/test_context_compressor.py"
    )

    with pytest.raises(RuntimeError, match="test_context_compressor.py"):
        acceptance.verify_acceptance_manifest(focused_tests=reduced_suite)


def test_reviewed_exemption_requires_an_explicit_review_rationale():
    with pytest.raises(RuntimeError, match="reviewed rationale"):
        acceptance.verify_acceptance_manifest(
            exemptions={"tests/unit/test_context_compressor.py": "temporary"},
        )


def test_task_10_matrix_has_24_scenarios_backed_by_fixed_suite_tests():
    assert set(acceptance.SCENARIO_EVIDENCE) == _EXPECTED_SCENARIOS
    covered = set(acceptance.ADAPTIVE_FOCUSED_TESTS)
    assert all(
        evidence and set(evidence) <= covered
        for evidence in acceptance.SCENARIO_EVIDENCE.values()
    )


def test_repository_gate_scrubs_provider_and_postgres_credentials():
    environment = acceptance.sanitized_test_environment(
        {
            "OPENAI_API_KEY": "must-not-propagate",
            "ANTHROPIC_API_KEY": "must-not-propagate",
            "POSTGRES_DSN": "must-not-propagate",
            "TASK8_PG_FAILURE_STORE_TESTS": "1",
            "openai_api_key": "case-insensitive-removal",
            "SAFE_VALUE": "preserved",
        }
    )

    assert environment["SAFE_VALUE"] == "preserved"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "POSTGRES_DSN" not in environment
    assert "TASK8_PG_FAILURE_STORE_TESTS" not in environment
    assert "openai_api_key" not in environment


def test_repository_gate_passes_only_sanitized_environment_to_pytest(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-propagate")
    monkeypatch.setenv("POSTGRES_DSN", "must-not-propagate")
    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    monkeypatch.setattr(acceptance.compileall, "compile_dir", lambda *args, **kwargs: True)

    acceptance.run_repository_gates(python="fixed-python")

    pytest_command, pytest_options = calls[0]
    assert pytest_command == [
        "fixed-python",
        "-m",
        "pytest",
        *acceptance.FOCUSED_TESTS,
        "-q",
        "-m",
        "not pg_runtime",
    ]
    assert "OPENAI_API_KEY" not in pytest_options["env"]
    assert "POSTGRES_DSN" not in pytest_options["env"]
    assert pytest_options["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert calls[1][0] == ["git", "diff", "--check"]
