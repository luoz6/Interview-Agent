import json

import pytest

from scripts.memory_validation_foundation_acceptance import (
    AcceptanceBlocked,
    SUCCESS_LINES,
    main,
    run_acceptance,
)


def evidence():
    return {
        "focused_tests": {"passed": True, "passed_count": 200},
        "pg_runtime": {"passed": True, "executed": 12},
        "full_python": {"passed": True, "passed_count": 1400, "failed": 0},
        "frontend_build": {"passed": True},
        "full_browser": {"passed": True, "scope": "full", "passed_count": 20, "failed": 0},
        "deletion_replay": {"passed": True},
        "durable_metrics": {"store_kind": "postgres_aggregate", "data_complete": True},
        "knowledge": {"ready": True, "corpus_version": "memory-p1-zh-v3"},
        "quality": {"passed": True},
        "privacy": {"passed": True},
        "compileall": {"passed": True},
        "diff_check": {"passed": True},
        "cleanup": {"passed": True},
        "production_observation": "NOT_RUN",
    }


def test_success_output_is_exact_and_keeps_consumption_blocked():
    assert run_acceptance(evidence()) == SUCCESS_LINES
    assert "PASS_FOR_PRODUCTION" not in "\n".join(SUCCESS_LINES)


def test_missing_operational_evidence_blocks_ready():
    payload = evidence()
    payload["full_browser"] = {"passed": False, "scope": "partial", "failed": 1}
    with pytest.raises(AcceptanceBlocked) as captured:
        run_acceptance(payload)
    assert "full_browser_not_green" in captured.value.codes
    assert "browser_scope_partial" in captured.value.codes


def test_cli_prints_only_exact_success_lines(tmp_path, capsys):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence()), encoding="utf-8")
    assert main(["--evidence", str(path)]) == 0
    assert capsys.readouterr().out.strip().splitlines() == list(SUCCESS_LINES)
