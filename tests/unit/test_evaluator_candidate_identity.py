from __future__ import annotations

import json
from pathlib import Path
import subprocess

from app.services.evaluator_candidate_identity import (
    EVALUATOR_CANDIDATE_IDENTITY_VERSION,
    capture_evaluator_candidate_identity,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "user.email", "evaluator@example.invalid")
    _git(root, "config", "user.name", "Evaluator Test")
    tracked = root / "app" / "tracked.py"
    tracked.parent.mkdir()
    tracked.write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(root, "add", "--", "app/tracked.py")
    _git(root, "commit", "--quiet", "-m", "base")
    return root


def test_clean_identity_is_stable_and_uses_explicit_null_empty_contract(tmp_path):
    root = _repository(tmp_path)

    first = capture_evaluator_candidate_identity(root)
    second = capture_evaluator_candidate_identity(root)

    assert first == second
    assert first.candidate_identity_version == EVALUATOR_CANDIDATE_IDENTITY_VERSION
    assert first.worktree_clean is True
    assert first.candidate_unstaged_tracked_diff_sha256 is None
    assert first.candidate_staged_diff_sha256 is None
    assert first.candidate_untracked_safe_manifest_sha256 is None
    assert first.candidate_untracked_safe_file_count == 0
    assert first.candidate_untracked_excluded_file_count == 0


def test_different_unstaged_and_staged_patches_change_their_own_identity(tmp_path):
    root = _repository(tmp_path)
    tracked = root / "app" / "tracked.py"

    tracked.write_text("VALUE = 'unstaged-one-unique'\n", encoding="utf-8")
    unstaged_one = capture_evaluator_candidate_identity(root)
    tracked.write_text("VALUE = 'unstaged-two-unique'\n", encoding="utf-8")
    unstaged_two = capture_evaluator_candidate_identity(root)
    assert (
        unstaged_one.candidate_unstaged_tracked_diff_sha256
        != unstaged_two.candidate_unstaged_tracked_diff_sha256
    )
    assert (
        unstaged_one.candidate_staged_diff_sha256
        == unstaged_two.candidate_staged_diff_sha256
    )

    _git(root, "add", "--", "app/tracked.py")
    staged_one = capture_evaluator_candidate_identity(root)
    tracked.write_text("VALUE = 'staged-two-unique'\n", encoding="utf-8")
    _git(root, "add", "--", "app/tracked.py")
    staged_two = capture_evaluator_candidate_identity(root)
    assert staged_one.candidate_staged_diff_sha256 != staged_two.candidate_staged_diff_sha256
    serialized = json.dumps(staged_two.manifest_fields(), sort_keys=True)
    assert "unstaged-one-unique" not in serialized
    assert "unstaged-two-unique" not in serialized
    assert "staged-two-unique" not in serialized
    assert "tracked.py" not in serialized


def test_untracked_manifest_is_safe_aggregate_without_content_or_paths(tmp_path):
    root = _repository(tmp_path)
    safe = root / "tests" / "new_case.py"
    safe.parent.mkdir()
    safe.write_text("SAFE_SENTINEL_ONE = True\n", encoding="utf-8")
    secret = root / "config" / ".env.secret"
    secret.parent.mkdir(exist_ok=True)
    secret.write_text("API_KEY=PRIVATE_SENTINEL\n", encoding="utf-8")

    first = capture_evaluator_candidate_identity(root)
    safe.write_text("SAFE_SENTINEL_TWO = True\n", encoding="utf-8")
    second = capture_evaluator_candidate_identity(root)

    assert first.worktree_clean is False
    assert first.candidate_untracked_safe_file_count == 1
    assert first.candidate_untracked_excluded_file_count == 1
    assert (
        first.candidate_untracked_safe_manifest_sha256
        != second.candidate_untracked_safe_manifest_sha256
    )
    serialized = json.dumps(second.manifest_fields(), sort_keys=True)
    for forbidden in (
        "SAFE_SENTINEL_ONE",
        "SAFE_SENTINEL_TWO",
        "PRIVATE_SENTINEL",
        "new_case.py",
        ".env.secret",
    ):
        assert forbidden not in serialized
