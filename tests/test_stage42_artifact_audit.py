import json
from pathlib import Path

import pytest

from scripts.audit_stage42_artifacts import (
    ArtifactAuditError,
    audit_release_artifacts,
    write_artifact_manifest,
)


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _make_run(tmp_path: Path, *, run_id: str = "stage42-run") -> Path:
    run = tmp_path / "reports" / "stage42-acceptance" / run_id
    _write(run / "metrics.json", json.dumps({"passed": True}))
    _write(run / "report.md", "# Stage 42 acceptance\n\nPASS\n")
    _write(run / "retrieval-cases" / "redis.json", '{"status":"passed"}')
    _write(run / "browser" / "desktop.png", b"safe-png-placeholder")
    return run


def test_manifest_has_relative_paths_sizes_and_sha256(tmp_path):
    run = _make_run(tmp_path)

    manifest = write_artifact_manifest(run, run_id="stage42-run")

    assert manifest["run_id"] == "stage42-run"
    assert [item["path"] for item in manifest["artifacts"]] == [
        "browser/desktop.png",
        "metrics.json",
        "report.md",
        "retrieval-cases/redis.json",
    ]
    assert all(item["size"] > 0 for item in manifest["artifacts"])
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"])
    assert audit_release_artifacts(run, expected_run_id="stage42-run") == manifest


@pytest.mark.parametrize(
    "sensitive_content",
    [
        "sk-stage42Secret123456",
        "postgresql://user:password@127.0.0.1/interview",
        r"F:\\agent\\Interview-Agent\\tmp\\trace.json",
        "/home/runner/interview/trace.json",
        "candidate@example.com",
        "+86 138-1234-5678",
    ],
)
def test_audit_rejects_secrets_paths_and_personal_information(
    tmp_path,
    sensitive_content,
):
    run = _make_run(tmp_path)
    _write(run / "report.md", sensitive_content)
    write_artifact_manifest(run, run_id="stage42-run")

    with pytest.raises(ArtifactAuditError, match="sensitive content"):
        audit_release_artifacts(run, expected_run_id="stage42-run")


def test_audit_rejects_unlisted_files_and_changed_artifacts(tmp_path):
    run = _make_run(tmp_path)
    write_artifact_manifest(run, run_id="stage42-run")
    _write(run / "provider-response.json", "{}")

    with pytest.raises(ArtifactAuditError, match="not whitelisted"):
        audit_release_artifacts(run, expected_run_id="stage42-run")

    (run / "provider-response.json").unlink()
    _write(run / "report.md", "changed after manifest")
    with pytest.raises(ArtifactAuditError, match="manifest mismatch"):
        audit_release_artifacts(run, expected_run_id="stage42-run")


def test_audit_requires_passing_metrics_and_nonempty_evidence_directories(tmp_path):
    run = _make_run(tmp_path)
    _write(run / "metrics.json", json.dumps({"passed": False}))
    write_artifact_manifest(run, run_id="stage42-run")

    with pytest.raises(ArtifactAuditError, match="passing release run"):
        audit_release_artifacts(run, expected_run_id="stage42-run")

    _write(run / "metrics.json", json.dumps({"passed": True}))
    (run / "browser" / "desktop.png").unlink()
    write_artifact_manifest(run, run_id="stage42-run")
    with pytest.raises(ArtifactAuditError, match="browser"):
        audit_release_artifacts(run, expected_run_id="stage42-run")
