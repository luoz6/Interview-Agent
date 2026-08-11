import hashlib
import json
from pathlib import Path

import pytest

from app.services.interview_quality_dataset import (
    InitialQuestionCaseInput,
    InterviewQualityDataset,
    load_interview_quality_dataset,
)


DATASET_DIR = Path("tests/golden/interview_quality_v1")
DATASET_FILES = {
    "initial-question-quality-v1": DATASET_DIR / "initial-question-quality-v1.json",
    "initial-question-quality-v2": DATASET_DIR / "initial-question-quality-v2.json",
    "followup-decision-quality-v1": DATASET_DIR / "followup-decision-quality-v1.json",
    "followup-decision-quality-v2": DATASET_DIR / "followup-decision-quality-v2.json",
    "report-score-quality-v2": DATASET_DIR / "report-score-quality-v2.json",
    "report-semantic-quality-v1": DATASET_DIR / "report-semantic-quality-v1.json",
}


def test_all_frozen_datasets_load_and_verify_hashes():
    loaded = {
        dataset_id: load_interview_quality_dataset(path)
        for dataset_id, path in DATASET_FILES.items()
    }

    assert set(loaded) == set(DATASET_FILES)
    for dataset_id, dataset in loaded.items():
        assert dataset.dataset_id == dataset_id
        assert dataset.dataset_version == dataset_id
        if dataset_id not in {
            "initial-question-quality-v2",
            "followup-decision-quality-v2",
        }:
            assert dataset.fixture_only is True
        assert all(not case.gate_eligible for case in dataset.cases)
        assert all(case.source_boundary.contains_real_candidate_data is False for case in dataset.cases)
        assert all(case.source_boundary.contains_principal_memory is False for case in dataset.cases)


def test_followup_v2_has_required_scale_sequences_adversarial_and_boundaries():
    dataset = load_interview_quality_dataset(
        DATASET_FILES["followup-decision-quality-v2"]
    )
    sequences = {
        case.input["sequence_id"]
        for case in dataset.cases
        if case.input.get("sequence_id")
    }
    adversarial = [
        case
        for case in dataset.cases
        if "adversarial" in case.input["scenario_tags"]
    ]

    assert len(dataset.cases) == 100
    assert len(sequences) == 20
    assert len(adversarial) >= 20
    assert dataset.fixture_only is False
    assert all(case.annotation.review_status == "pending" for case in dataset.cases)
    assert all(case.gate_eligible is False for case in dataset.cases)


def test_initial_question_v2_has_t57_scale_stratification_and_repeat_budget():
    dataset = load_interview_quality_dataset(
        DATASET_FILES["initial-question-quality-v2"]
    )
    inputs = [InitialQuestionCaseInput.model_validate(case.input) for case in dataset.cases]

    assert len(dataset.cases) == 12
    assert dataset.fixture_only is False
    assert {item.scenario_domain for item in inputs} == {
        "backend",
        "frontend",
        "data",
        "platform",
        "general_project",
        "system_design",
    }
    assert {case.difficulty for case in dataset.cases} == {
        "foundation",
        "intermediate",
        "advanced",
    }
    assert {item.configuration["focus_preset"] for item in inputs} == {
        "technical_depth",
        "system_design",
        "project_review",
        "balanced",
    }
    assert {item.configuration["target_duration_minutes"] for item in inputs} == {
        15,
        30,
        45,
        60,
    }
    assert all(item.runs_per_case >= 2 for item in inputs)
    assert sum(case.language == "zh-Hans" for case in dataset.cases) > 6
    assert {case.partition for case in dataset.cases} == {
        "train",
        "dev",
        "blind-test",
    }
    assert all(case.annotation.review_status == "pending" for case in dataset.cases)
    assert all(case.gate_eligible is False for case in dataset.cases)


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
