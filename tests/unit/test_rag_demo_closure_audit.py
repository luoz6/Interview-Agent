import subprocess
from pathlib import Path

from scripts.audit_rag_demo_closure import (
    ROOT,
    _check_untracked_files,
    _has_supported_plan_status,
    _has_whitespace_error,
    run_closure_audit,
)


def test_non_git_closure_audit_covers_current_contracts():
    checks = run_closure_audit(ROOT, include_git=False)
    by_id = {check.check_id: check for check in checks}

    expected = {
        "closure.plan_repository_local",
        "closure.fusion_contract_nullable",
        "closure.diagnostic_variant_identity",
        "closure.inspector_controlled_fusion_modes",
        "closure.current_docs_and_history_archive",
    }
    assert expected <= by_id.keys()
    assert all(by_id[check_id].passed for check_id in expected), [
        by_id[check_id] for check_id in expected if not by_id[check_id].passed
    ]


def test_git_closure_audit_is_baseline_aware_and_branch_neutral():
    checks = run_closure_audit(ROOT, include_git=True)
    by_id = {check.check_id: check for check in checks}

    assert by_id["git.baseline_ancestor"].passed
    assert by_id["git.diff_check"].passed
    assert by_id["git.worktree_state_reported"].passed
    assert all(check.passed for check in checks), [
        check for check in checks if not check.passed
    ]


def test_closure_audit_source_has_no_external_plan_or_branch_identity():
    source = Path("scripts/audit_rag_demo_closure.py").read_text(encoding="utf-8")

    forbidden = (
        "Down" + "loads",
        "EXPECTED_PLAN_" + "SHA256",
        "master_not_" + "modified",
        "branch " + "!=",
    )
    assert not [marker for marker in forbidden if marker in source]


def test_closure_plan_status_accepts_execution_and_completion_states():
    assert _has_supported_plan_status("> 状态：`IN_EXECUTION`")
    assert _has_supported_plan_status("> 状态：`COMPLETED`")
    assert not _has_supported_plan_status("> 状态：`READY_FOR_EXECUTION`")


def test_whitespace_error_detection_ignores_line_ending_warnings():
    assert _has_whitespace_error("file.txt:1: trailing whitespace.")
    assert _has_whitespace_error("file.txt:2: new blank line at EOF.")
    assert not _has_whitespace_error(
        "warning: LF will be replaced by CRLF the next time Git touches it"
    )


def test_untracked_file_diff_check_covers_clean_and_invalid_files(tmp_path):
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "clean.txt").write_text("clean\n", encoding="utf-8")

    passed, evidence = _check_untracked_files(tmp_path)

    assert passed, evidence
    assert "1 untracked files checked" in evidence

    (tmp_path / "invalid.txt").write_text("invalid  \n", encoding="utf-8")

    passed, evidence = _check_untracked_files(tmp_path)

    assert not passed
    assert "invalid.txt" in evidence
    assert "trailing whitespace" in evidence
