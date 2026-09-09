from __future__ import annotations

from pathlib import Path

import pytest

from app.services.cross_question_report_diagnostics import (
    RM4B_COMPLETION_STATUS,
    build_rm4b_artifact,
    default_rm4b_fixture_path,
    read_and_validate_rm4b_artifacts,
    replay_rm4b_artifacts,
    write_rm4b_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = default_rm4b_fixture_path(ROOT)


def test_rm4b_build_write_validate_and_replay(tmp_path):
    artifact, observer = build_rm4b_artifact(FIXTURE)
    assert artifact["status"] == RM4B_COMPLETION_STATUS
    assert len(artifact["question_ids"]) >= 2
    assert observer["review_scope"] == "development_diagnostic"
    assert observer["reviewer_kind"] == "diagnostic_observer"
    assert observer["formal_evidence_eligible"] is False

    write_rm4b_artifacts(tmp_path, artifact, observer)
    saved_artifact, saved_observer = read_and_validate_rm4b_artifacts(tmp_path)
    assert saved_artifact == artifact
    assert saved_observer == observer
    replay_rm4b_artifacts(fixture_path=FIXTURE, output_dir=tmp_path)


def test_rm4b_replay_rejects_tampered_artifact(tmp_path):
    artifact, observer = build_rm4b_artifact(FIXTURE)
    write_rm4b_artifacts(tmp_path, artifact, observer)
    path = tmp_path / "multi-question-artifact.json"
    payload = path.read_text(encoding="utf-8").replace('"multi_question": true', '"multi_question": false')
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        read_and_validate_rm4b_artifacts(tmp_path)
