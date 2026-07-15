import json
from pathlib import Path

from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_eval_case_builder import build_report_evaluation_input
from app.services.report_eval_dataset import load_evaluation_dataset
from app.services.report_eval_metrics import calculate_metrics
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_VERSION,
    DimensionEvidence,
    score_question_from_evidence,
)


def rescore_run(*, run_dir: Path, dataset_path: Path) -> dict:
    store = EvaluationArtifactStore.open(run_dir)
    dataset = load_evaluation_dataset(dataset_path)
    cases = {case.case_id: case for case in dataset.cases}

    for normalized_path in sorted(run_dir.glob("attempts/*/run-*/normalized.json")):
        attempt = json.loads(normalized_path.read_text(encoding="utf-8"))
        case = cases[attempt["case_id"]]
        trace_path = next(normalized_path.parent.rglob("*_normalized_payload.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        question_result = trace["payload"]["question_results"][0]
        evidence = [
            DimensionEvidence.model_validate(item)
            for item in question_result.get("dimension_evidence", [])
        ]
        _, evaluation_items = build_report_evaluation_input(case)
        score = score_question_from_evidence(evaluation_items[0], evidence)
        attempt["score"] = score.score
        attempt["applicable_dimensions"] = score.applicable_dimensions
        store.write_attempt(case.case_id, attempt["run_number"], normalized=attempt)

    attempts = store.load_normalized_attempts()
    manifest = store.read_manifest()
    expected_attempts = int(manifest["target_attempts"])
    metrics = calculate_metrics(
        attempts,
        expected_attempt_count=expected_attempts,
    ).model_dump(mode="json")
    store.write_metrics(metrics)
    manifest["rubric_version"] = REPORT_SCORING_RUBRIC_VERSION
    manifest["decision"] = "PASS" if metrics["passed"] else "FAIL"
    manifest["rescored_from_saved_evidence"] = True
    store.write_manifest(manifest)
    return metrics
