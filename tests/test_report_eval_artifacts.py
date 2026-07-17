import json

import pytest

from app.services.report_eval_artifacts import EvaluationArtifactStore


def test_attempt_is_saved_and_removed_from_pending(tmp_path):
    store = EvaluationArtifactStore.create(
        root=tmp_path,
        run_id="run-1",
        manifest={
            "case_ids": ["c1"],
            "runs_per_case": 2,
            "base_url": "https://api.example.com/v1",
        },
    )
    attempt_dir = store.attempt_directory("c1", 1)
    assert attempt_dir == store.run_dir / "attempts/c1/run-1"

    store.write_attempt("c1", 1, normalized={"score": 80})

    assert store.pending_attempts(["c1"], runs_per_case=2) == [("c1", 2)]
    manifest = store.read_manifest()
    assert manifest["base_url_host"] == "api.example.com"
    assert "base_url" not in manifest


def test_atomic_json_and_markdown_files_are_valid_after_each_write(tmp_path):
    store = EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})

    store.write_attempt("c1", 1, normalized={"score": 80})
    store.write_metrics({"passed": True})
    store.write_report("# PASS\n")

    normalized_path = store.run_dir / "attempts/c1/run-1/normalized.json"
    assert json.loads(normalized_path.read_text(encoding="utf-8")) == {"score": 80}
    assert json.loads((store.run_dir / "metrics.json").read_text(encoding="utf-8")) == {"passed": True}
    assert (store.run_dir / "report.md").read_text(encoding="utf-8") == "# PASS\n"
    assert list(store.run_dir.rglob("*.tmp")) == []


def test_error_artifact_does_not_mark_attempt_complete(tmp_path):
    store = EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})

    store.write_error("c1", 1, {"error_type": "ValueError", "message": "bad payload"})

    assert store.pending_attempts(["c1"], runs_per_case=1) == [("c1", 1)]


def test_open_requires_existing_manifest(tmp_path):
    with pytest.raises(FileNotFoundError, match="manifest"):
        EvaluationArtifactStore.open(tmp_path / "missing")


def test_load_normalized_attempts_uses_case_and_run_order(tmp_path):
    store = EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})
    store.write_attempt("c2", 1, normalized={"case_id": "c2", "run_number": 1})
    store.write_attempt("c1", 2, normalized={"case_id": "c1", "run_number": 2})
    store.write_attempt("c1", 1, normalized={"case_id": "c1", "run_number": 1})

    assert store.load_normalized_attempts() == [
        {"case_id": "c1", "run_number": 1},
        {"case_id": "c1", "run_number": 2},
        {"case_id": "c2", "run_number": 1},
    ]
