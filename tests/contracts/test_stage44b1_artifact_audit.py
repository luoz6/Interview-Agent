import json
from pathlib import Path

import pytest

from scripts.release_artifact_audit import (
    ArtifactAuditError,
    audit_stage44b1_artifacts,
    write_stage44b1_manifest as write_artifact_manifest,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_run(tmp_path: Path, *, run_id="stage44b1-run") -> Path:
    run_dir = tmp_path / "reports" / "stage44b1-acceptance" / run_id
    write(
        run_dir / "metrics.json",
        json.dumps(
            {
                "passed": True,
                "run_id": run_id,
                "storage_strategy": "exact_pgvector_cosine",
            }
        ),
    )
    write(run_dir / "report.md", "# Stage 44B1 acceptance\n\nPASS\n")
    write(
        run_dir / "retrieval-cases" / "pilot-v2" / "redis.json",
        json.dumps(
            {
                "case_id": "redis",
                "status": "completed",
                "retrieved_ids": ["redis_consistency"],
                "scores": {"redis_consistency": 0.9},
                "latency_ms": 1.0,
            }
        ),
    )
    return run_dir


def test_manifest_reuses_relative_whitelisted_hash_inventory(tmp_path):
    run_dir = make_run(tmp_path)

    manifest = write_artifact_manifest(run_dir, run_id="stage44b1-run")

    assert [item["path"] for item in manifest["artifacts"]] == [
        "metrics.json",
        "report.md",
        "retrieval-cases/pilot-v2/redis.json",
    ]
    assert all(item["size"] > 0 for item in manifest["artifacts"])
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"])
    assert audit_stage44b1_artifacts(
        run_dir, expected_run_id="stage44b1-run"
    ) == manifest


@pytest.mark.parametrize(
    "blocked_key",
    [
        "url",
        "references",
        "source_url",
        "question_patterns",
        "query_text",
        "content",
    ],
)
def test_v2_audit_rejects_source_query_and_content_keys(tmp_path, blocked_key):
    run_dir = make_run(tmp_path)
    write(
        run_dir / "retrieval-cases" / "pilot-v2" / "redis.json",
        json.dumps({"safe": {blocked_key: "redacted-value"}}),
    )
    write_artifact_manifest(run_dir, run_id="stage44b1-run")

    with pytest.raises(ArtifactAuditError, match="blocked"):
        audit_stage44b1_artifacts(run_dir, expected_run_id="stage44b1-run")


@pytest.mark.parametrize(
    "sensitive_content",
    [
        "sk-stage44b1Secret123456",
        "Authorization: Bearer hidden-token",
        "postgresql://user:password@127.0.0.1/interview",
        "redis://:password@127.0.0.1:6379/0",
        r"F:\agent\Interview-Agent\private.json",
        "/home/runner/interview/private.json",
        "candidate@example.com",
        "+86 138-1234-5678",
        "https://example.com/private-source",
    ],
)
def test_audit_rejects_secrets_paths_and_personal_information(
    tmp_path, sensitive_content
):
    run_dir = make_run(tmp_path)
    write(run_dir / "report.md", sensitive_content)
    write_artifact_manifest(run_dir, run_id="stage44b1-run")

    with pytest.raises(ArtifactAuditError, match="sensitive content"):
        audit_stage44b1_artifacts(run_dir, expected_run_id="stage44b1-run")


def test_audit_rejects_unexpected_changed_and_nonpassing_artifacts(tmp_path):
    run_dir = make_run(tmp_path)
    write_artifact_manifest(run_dir, run_id="stage44b1-run")
    write(run_dir / "provider-response.json", "{}")
    with pytest.raises(ArtifactAuditError, match="not whitelisted"):
        audit_stage44b1_artifacts(run_dir, expected_run_id="stage44b1-run")

    (run_dir / "provider-response.json").unlink()
    write(run_dir / "report.md", "changed after manifest")
    with pytest.raises(ArtifactAuditError, match="manifest mismatch"):
        audit_stage44b1_artifacts(run_dir, expected_run_id="stage44b1-run")

    write(run_dir / "metrics.json", json.dumps({"passed": False}))
    write_artifact_manifest(run_dir, run_id="stage44b1-run")
    with pytest.raises(ArtifactAuditError, match="passing release run"):
        audit_stage44b1_artifacts(run_dir, expected_run_id="stage44b1-run")
