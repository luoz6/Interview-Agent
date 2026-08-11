from __future__ import annotations

from scripts import context_compression_repository_acceptance as acceptance


def test_versioned_repository_acceptance_emits_only_shadow_status(capsys):
    assert acceptance.main(["--skip-tests"]) == 0
    assert capsys.readouterr().out == "READY_FOR_SHADOW\n"


def test_versioned_repository_acceptance_uses_adaptive_fixed_suite():
    assert acceptance.FOCUSED_TESTS
    assert "tests/unit/test_context_compression_runner.py" in acceptance.FOCUSED_TESTS
    assert "tests/unit/test_context_compression_failure_containment.py" in (
        acceptance.FOCUSED_TESTS
    )
    assert "tests/acceptance/test_memory_system_optimization_acceptance.py" in (
        acceptance.FOCUSED_TESTS
    )
    acceptance.verify_acceptance_manifest(
        focused_tests=acceptance.FOCUSED_TESTS,
        exemptions=acceptance.REVIEWED_TEST_EXEMPTIONS,
        scenario_evidence=acceptance.SCENARIO_EVIDENCE,
    )


def test_versioned_repository_gate_reuses_sanitized_environment():
    environment = acceptance.sanitized_test_environment(
        {
            "OPENAI_API_KEY": "must-not-propagate",
            "POSTGRES_DSN": "must-not-propagate",
            "SAFE_VALUE": "preserved",
        }
    )

    assert environment["SAFE_VALUE"] == "preserved"
    assert "OPENAI_API_KEY" not in environment
    assert "POSTGRES_DSN" not in environment
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
