import json
from pathlib import Path

import pytest

from scripts.audit_stage40_artifacts import (
    ArtifactAuditError,
    audit_release_artifacts,
    build_artifact_manifest,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_manifest_only_contains_release_whitelist_and_has_sha256(tmp_path):
    run = tmp_path / "reports" / "stage40-acceptance" / "run-1"
    _write(run / "manifest.json", '{"run_id":"run-1"}')
    _write(run / "metrics.json", '{"passed":true}')
    _write(run / "report.md", "PASS")
    _write(run / "attempts" / "case-1" / "run-1" / "normalized.json", "{}")
    _write(tmp_path / "reports" / "stage40-smoke" / "scratch.json", "{}")

    manifest = build_artifact_manifest(run, root=tmp_path)

    assert [item["path"] for item in manifest["files"]] == [
        "reports/stage40-acceptance/run-1/attempts/case-1/run-1/normalized.json",
        "reports/stage40-acceptance/run-1/manifest.json",
        "reports/stage40-acceptance/run-1/metrics.json",
        "reports/stage40-acceptance/run-1/report.md",
    ]
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])


def test_audit_rejects_sensitive_artifact_content(tmp_path):
    run = tmp_path / "run"
    _write(run / "manifest.json", '{"run_id":"run"}')
    _write(run / "metrics.json", '{"passed":true}')
    _write(run / "report.md", "authorization: Bearer secret-token")
    _write(run / "attempts" / "case" / "run-1" / "normalized.json", "{}")

    with pytest.raises(ArtifactAuditError, match="sensitive content"):
        audit_release_artifacts(run, expected_run_id="run")


def test_audit_requires_consistent_run_id_and_passing_metrics(tmp_path):
    run = tmp_path / "run"
    _write(run / "manifest.json", json.dumps({"run_id": "another-run"}))
    _write(run / "metrics.json", json.dumps({"passed": False}))
    _write(run / "report.md", "FAIL")
    _write(run / "attempts" / "case" / "run-1" / "normalized.json", "{}")

    with pytest.raises(ArtifactAuditError, match="run id"):
        audit_release_artifacts(run, expected_run_id="run")
