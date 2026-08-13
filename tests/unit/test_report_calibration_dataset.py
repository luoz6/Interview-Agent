from collections import Counter
import json
from pathlib import Path

import pytest

from app.services.report_calibration_dataset import (
    calibration_dataset_sha256,
    load_calibration_dataset,
)
from scripts.build_report_calibration_dataset import build_dataset


DATASET_PATH = Path(
    "tests/golden/interview_quality_v1/report-score-calibration-v1.json"
)


def test_calibration_dataset_has_80_synthetic_cases_and_balanced_strata():
    dataset = load_calibration_dataset(DATASET_PATH)

    assert len(dataset.cases) == 80
    assert sum(case.partition == "blind" for case in dataset.cases) == 20
    assert Counter(case.question_type for case in dataset.cases) == {
        "technical": 20,
        "system_design": 20,
        "project_review": 20,
        "behavioral": 20,
    }
    assert min(Counter(case.language for case in dataset.cases).values()) >= 24
    assert all(case.source_classification == "synthetic" for case in dataset.cases)
    assert not any(case.contains_real_candidate_data for case in dataset.cases)
    assert not any(case.contains_principal_memory for case in dataset.cases)
    medium_cases = [case for case in dataset.cases if case.quality_label == "medium"]
    assert len(medium_cases) == 20
    assert all(case.required_missing_points for case in medium_cases)
    assert not any(
        case.required_missing_points
        for case in dataset.cases
        if case.quality_label == "strong"
    )


def test_calibration_dataset_is_not_gate_eligible_before_independent_review():
    dataset = load_calibration_dataset(DATASET_PATH)

    assert dataset.review_status == "PENDING_INDEPENDENT_REVIEW"
    assert dataset.gate_eligible is False
    with pytest.raises(ValueError, match="independent review"):
        dataset.require_gate_eligible()


def test_checked_in_dataset_matches_deterministic_builder():
    expected = build_dataset().model_dump(mode="json")
    actual = load_calibration_dataset(DATASET_PATH).model_dump(mode="json")

    assert actual == expected
    digest = calibration_dataset_sha256(DATASET_PATH)
    assert len(digest) == 64
    manifest = json.loads(
        DATASET_PATH.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["dataset_sha256"] == digest
    assert manifest["case_count"] == 80
    assert manifest["blind_case_count"] == 20
    assert manifest["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert manifest["gate_eligible"] is False
