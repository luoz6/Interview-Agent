from pathlib import Path

import pytest

from app.services.report_calibration_dataset import load_calibration_dataset
from app.services.report_calibration_runner import evaluate_calibration_dataset


DATASET_PATH = Path(
    "tests/golden/interview_quality_v1/report-score-calibration-v1.json"
)


def test_unreviewed_dev_calibration_is_diagnostic_and_uses_zero_provider_calls():
    dataset = load_calibration_dataset(DATASET_PATH)

    result = evaluate_calibration_dataset(
        dataset,
        partition="dev",
        allow_unreviewed_dev=True,
    )

    assert result.provider_invocations == 0
    assert result.partition == "dev"
    assert result.dataset_review_status == "PENDING_INDEPENDENT_REVIEW"
    assert result.rubric_version.endswith("candidate")
    assert len(result.rubric_sha256) == 64
    assert result.metrics.completed_attempt_count == 60
    assert result.metrics.evidence_grounding_rate == 1.0
    assert result.metrics.expected_range_attempt_hit_rate == 1.0
    assert result.error_categories == {}


def test_blind_partition_cannot_run_before_independent_review():
    dataset = load_calibration_dataset(DATASET_PATH)

    with pytest.raises(ValueError, match="independent review"):
        evaluate_calibration_dataset(dataset, partition="blind")


def test_all_partition_cannot_bypass_pending_disputes_or_review():
    dataset = load_calibration_dataset(DATASET_PATH)

    with pytest.raises(ValueError, match="not gate eligible"):
        evaluate_calibration_dataset(
            dataset,
            partition="all",
            allow_unreviewed_dev=True,
        )
