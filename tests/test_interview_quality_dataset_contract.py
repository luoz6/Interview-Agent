import hashlib
import json
from pathlib import Path

import pytest

from app.services.interview_quality_dataset import (
    InterviewQualityDataset,
    load_interview_quality_dataset,
)


DATASET_DIR = Path("tests/golden/interview_quality_v1")
DATASET_FILES = {
    "initial-question-quality-v1": DATASET_DIR / "initial-question-quality-v1.json",
    "followup-decision-quality-v1": DATASET_DIR / "followup-decision-quality-v1.json",
    "report-score-quality-v2": DATASET_DIR / "report-score-quality-v2.json",
    "report-semantic-quality-v1": DATASET_DIR / "report-semantic-quality-v1.json",
}


def test_all_four_frozen_dataset_contract_fixtures_load_and_verify_hashes():
    loaded = {
        dataset_id: load_interview_quality_dataset(path)
        for dataset_id, path in DATASET_FILES.items()
    }

    assert set(loaded) == set(DATASET_FILES)
    for dataset_id, dataset in loaded.items():
        assert dataset.dataset_id == dataset_id
        assert dataset.dataset_version == dataset_id
        assert dataset.fixture_only is True
        assert all(not case.gate_eligible for case in dataset.cases)
        assert all(case.source_boundary.contains_real_candidate_data is False for case in dataset.cases)
        assert all(case.source_boundary.contains_principal_memory is False for case in dataset.cases)


def test_report_score_uses_range_and_other_datasets_use_action():
    for dataset_id, path in DATASET_FILES.items():
        case = load_interview_quality_dataset(path).cases[0]
        if dataset_id == "report-score-quality-v2":
            assert case.expectation.score_range is not None
            assert case.expectation.action is None
        else:
            assert case.expectation.action is not None
            assert case.expectation.score_range is None


def test_unreviewed_or_open_dispute_case_cannot_enter_blocking_gate():
    payload = json.loads(DATASET_FILES["initial-question-quality-v1"].read_text(encoding="utf-8"))
    payload["cases"][0]["gate_eligible"] = True

    with pytest.raises(ValueError, match="unreviewed cases cannot be gate eligible"):
        InterviewQualityDataset.model_validate(payload)


def test_overall_file_hash_manifest_matches_every_dataset_file():
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "interview-quality-dataset-file-manifest-v1"
    assert set(manifest["files"]) == {path.name for path in DATASET_FILES.values()}
    for name, expected in manifest["files"].items():
        actual = hashlib.sha256((DATASET_DIR / name).read_bytes()).hexdigest()
        assert actual == expected
