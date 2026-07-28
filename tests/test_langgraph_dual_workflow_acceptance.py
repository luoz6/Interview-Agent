from pathlib import Path
import subprocess
from types import SimpleNamespace

from scripts.langgraph_dual_workflow_acceptance import (
    SCHEMA_VERSION,
    focused_commands,
    run_acceptance,
    write_artifacts,
)


def test_focused_check_set_is_versioned_and_complete():
    checks = {
        check
        for command in focused_commands()
        for check in command.checks
    }

    assert SCHEMA_VERSION == "langgraph-dual-release-acceptance-v1"
    assert {
        "interview_focused_contracts",
        "interview_postgres_restart_recovery",
        "review_focused_regression",
        "review_postgres_restart_recovery",
        "assignment_matrix",
        "rollback_existing_interview_resume",
        "rollback_existing_review_resume",
        "joint_postgres_handoff",
        "review_cold_start_fenced",
        "shared_saver_namespace_isolation",
        "wrong_engine_events_discarded",
        "out_of_order_command_rejected",
        "retention_maintenance_active",
        "runtime_preflight_zero_zero",
        "runtime_preflight_interview_only",
        "runtime_preflight_review_only",
        "runtime_preflight_joint",
    }.issubset(checks)


def test_successful_focused_run_is_repository_only(monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://configured")

    def runner(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="5 passed, 1 skipped",
            stderr="",
        )

    result = run_acceptance(mode="focused", timeout=10, runner=runner)

    assert result["repository_status"] == "PASS"
    assert result["operator_canary_status"] == "NOT_RUN"
    assert result["privacy_result"] == "PASS"
    assert result["test_counts"]["passed"] > 0


def test_missing_postgres_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    result = run_acceptance(mode="focused", timeout=10)

    assert result["repository_status"] == "FAIL"
    assert result["checks"] == [
        {"name": "postgres_configuration", "status": "FAIL"}
    ]


def test_timeout_creates_fail_without_raw_subprocess_output(monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://configured")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd="private command", timeout=kwargs["timeout"]
        )

    result = run_acceptance(mode="focused", timeout=1, runner=timeout)

    assert result["repository_status"] == "FAIL"
    assert "private command" not in str(result)


def test_acceptance_artifacts_are_sanitized(tmp_path):
    result = {
        "schema_version": SCHEMA_VERSION,
        "repository_status": "PASS",
        "operator_canary_status": "NOT_RUN",
        "generated_at": "2026-07-25T00:00:00Z",
        "commit_id": "abcdef1",
        "duration_seconds": 1.25,
        "test_counts": {"passed": 10, "skipped": 1},
        "checks": [{"name": "privacy_allowlist", "status": "PASS"}],
        "privacy_result": "PASS",
    }

    write_artifacts(result, tmp_path)

    combined = (tmp_path / "result.json").read_text(
        encoding="utf-8"
    ) + (tmp_path / "result.md").read_text(encoding="utf-8")
    for forbidden in (
        "postgresql://",
        "checkpoint_id",
        "private answer",
        "provider payload",
    ):
        assert forbidden not in combined
