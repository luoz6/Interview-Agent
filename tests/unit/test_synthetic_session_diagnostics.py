from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.synthetic_session_diagnostics import (
    RM5_COMPLETION_STATUS,
    RM5_FAILURE_STATUS,
    build_rm5_artifact,
    default_rm5_scenario_path,
    replay_rm5_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = default_rm5_scenario_path(ROOT)


def test_rm5_offline_artifact_is_multi_question_and_zero_call(tmp_path):
    manifest = build_rm5_artifact(
        scenario_path=SCENARIOS,
        output_dir=tmp_path,
        run_id="test-rm5",
    )

    assert manifest["status"] == RM5_COMPLETION_STATUS
    assert manifest["terminal"] is True
    assert manifest["completed_scenario_count"] == 6
    assert manifest["provider_called"] is False
    assert manifest["provider_invocations"] == 0
    assert manifest["real_postgres_dependency"] is False
    assert manifest["formal_evidence_eligible"] is False

    records = list((tmp_path / "session-records").glob("*.json"))
    reports = list((tmp_path / "multi-question-artifacts").glob("*.json"))
    assert len(records) == len(reports) == 6
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert len(record["question_ids"]) == 3
        assert len(record["decisions"]) == 3
        assert all(item["decision_action"] == "next_question" for item in record["decisions"])
        assert record["provider_invocations"] == 0
        assert record["diagnostic_only"] is True
        assert record["formal_evidence_eligible"] is False

    observer = json.loads((tmp_path / "diagnostic-observer.json").read_text(encoding="utf-8"))
    assert observer["review_scope"] == "development_diagnostic"
    assert observer["reviewer_kind"] == "diagnostic_observer"
    assert observer["formal_evidence_eligible"] is False


def test_rm5_replay_and_resume_after_terminal_failure(tmp_path):
    failed = build_rm5_artifact(
        scenario_path=SCENARIOS,
        output_dir=tmp_path,
        run_id="test-rm5-resume",
        fail_after=2,
    )
    assert failed["status"] == RM5_FAILURE_STATUS
    assert failed["terminal"] is True
    assert failed["completed_scenario_count"] == 2
    assert failed["failure_code"] == "INJECTED_FAILURE_FOR_RESUME_CONTRACT"

    resumed = build_rm5_artifact(
        scenario_path=SCENARIOS,
        output_dir=tmp_path,
        run_id="test-rm5-resume",
        resume=True,
    )
    assert resumed["status"] == RM5_COMPLETION_STATUS
    assert resumed["resumed"] is True
    assert resumed["completed_scenario_count"] == 6
    assert replay_rm5_artifact(scenario_path=SCENARIOS, output_dir=tmp_path)["status"] == RM5_COMPLETION_STATUS


def test_rm5_rejects_incomplete_selection(tmp_path):
    with pytest.raises(ValueError, match="6 to 8"):
        build_rm5_artifact(
            scenario_path=SCENARIOS,
            output_dir=tmp_path,
            scenario_ids=["redis-cache-consistency"],
        )
