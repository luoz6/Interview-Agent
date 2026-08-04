from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "local-v1-long-term-memory-rc-manifest.json"
HANDOFF_PATH = ROOT / "docs" / "local-v1-long-term-memory-rc-handoff.md"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_rc_manifest_hashes_the_declared_implementation_scope():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    base = manifest["base_revision"]
    subject = manifest["task_13_evidence_revision"]
    paths = sorted(
        line
        for line in _git("diff", "--name-only", base, subject).splitlines()
        if line
    )
    entries = [f'{_git("rev-parse", f"{subject}:{path}")} {path}' for path in paths]
    canonical = "\n".join(entries) + "\n"

    assert len(paths) == manifest["scope"]["changed_path_count"]
    assert hashlib.sha256(canonical.encode()).hexdigest() == manifest["scope"][
        "canonical_git_blob_path_sha256"
    ]


def test_rc_manifest_critical_file_hashes_match_subject_revision():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    subject = manifest["task_13_evidence_revision"]

    for path, expected in manifest["critical_files"].items():
        content = subprocess.run(
            ["git", "show", f"{subject}:{path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert hashlib.sha256(content).hexdigest() == expected


def test_rc_manifest_preserves_safe_boundaries_and_public_artifact_privacy():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    boundaries = manifest["boundaries"]

    assert boundaries == {
        "hosted_v2": "NO_GO_FOR_NOW",
        "default_mode": "DISABLED",
        "consumption": "AVAILABLE_BUT_DEFAULT_OFF",
        "scoring_and_report_use": "PROHIBITED",
        "real_candidate_production_processing": "PROHIBITED",
        "real_provider_evaluation": "NOT_RUN",
    }
    assert manifest["publication_state"] == "HARDENED_CANDIDATE_REMOTE_VERIFIED"
    assert manifest["final_commit_gate"] == (
        "EXACT_EVIDENCE_RETEST_AND_REMOTE_MATCH_REQUIRED"
    )
    assert manifest["rc_candidate"] == {
        "revision": "3d4dccbb38afcf9792f368b0a2ff4a3146f0d1be",
        "full_python_postgres_passed": 2123,
        "full_python_skipped": 1,
        "frontend_build_passed": True,
        "full_browser_passed": 86,
        "full_browser_skipped": 38,
        "postgres_test_relation_residue": 0,
        "test_listener_residue": 0,
        "remote_branch_verified": True,
    }
    rendered = MANIFEST_PATH.read_text(encoding="utf-8") + HANDOFF_PATH.read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "postgresql://",
        "OPENAI_API_KEY=",
        "local-owner",
        "principal_id",
        "fact_id",
        "session_id",
        "source_manifest_sha256",
        "source_excerpt_sha256",
        "BEGIN PRIVATE KEY",
    ):
        assert forbidden not in rendered


def test_rc_handoff_requires_exact_retest_push_and_remote_verification():
    text = HANDOFF_PATH.read_text(encoding="utf-8")
    for expected in (
        "full Python/PostgreSQL",
        "frontend build",
        "browser matrix",
        "zero listeners",
        "zero isolated test relations",
        "Push `codex/local-v1-long-term-memory`",
        "verify the final remote hash",
        "real-candidate production processing",
    ):
        assert expected in text
