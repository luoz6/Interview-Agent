import json
from pathlib import Path

from app.services.interview_quality_gate import evaluate_metric, load_gate_config


RUN_DIR = Path("reports/stage40-acceptance/20260710T124843Z")
AUGMENTATION_PATH = Path(
    "reports/stage40-acceptance/expected-range-augmentation-v1.json"
)


def test_saved_stage40_attempts_recompute_to_twenty_eight_of_forty_and_fail():
    augmentation = json.loads(AUGMENTATION_PATH.read_text(encoding="utf-8"))
    expected_ranges = augmentation["expected_ranges"]
    attempts = []
    for path in sorted((RUN_DIR / "attempts").glob("*/run-*/normalized.json")):
        attempt = json.loads(path.read_text(encoding="utf-8"))
        low, high = expected_ranges[attempt["case_id"]]
        attempts.append(low <= attempt["score"] <= high)

    hit_count = sum(attempts)
    hit_rate = hit_count / len(attempts)
    gate = evaluate_metric(
        load_gate_config(),
        augmentation["gate_metric"],
        actual=hit_rate,
        sample_size=len(attempts),
    )

    assert len(attempts) == augmentation["observed"]["attempt_count"] == 40
    assert hit_count == augmentation["observed"]["interval_hit_count"] == 28
    assert hit_rate == augmentation["observed"]["interval_hit_rate"] == 0.70
    assert gate.status == augmentation["observed"]["expected_gate_status"] == "FAIL"


def test_augmentation_preserves_all_twenty_dataset_case_identities():
    dataset = json.loads(Path("tests/golden/report_quality_v1.json").read_text(encoding="utf-8"))
    augmentation = json.loads(AUGMENTATION_PATH.read_text(encoding="utf-8"))

    assert set(augmentation["expected_ranges"]) == {
        case["case_id"] for case in dataset["cases"]
    }
