import json
import hashlib
from pathlib import Path

from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_eval_case_builder import build_report_evaluation_input
from app.services.report_eval_dataset import load_evaluation_dataset
from app.services.report_eval_metrics import calculate_metrics
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
    DimensionEvidence,
    score_question_from_evidence,
)


def rescore_run(
    *,
    run_dir: Path,
    dataset_path: Path,
    output_dir: Path | None = None,
) -> dict:
    source_store = EvaluationArtifactStore.open(
        root=run_dir.parent,
        run_id=run_dir.name,
    )
    dataset = load_evaluation_dataset(dataset_path)
    cases = {case.case_id: case for case in dataset.cases}
    target_dir = output_dir or run_dir.with_name(
        f"{run_dir.name}-{REPORT_SCORING_RUBRIC_VERSION}-replay"
    )
    if target_dir.exists():
        raise FileExistsError(f"replay artifact already exists: {target_dir}")
    source_manifest_bytes = (run_dir / "manifest.json").read_bytes()
    source_attempt_paths = sorted(run_dir.glob("attempts/*/run-*/normalized.json"))
    source_attempt_hash = hashlib.sha256(
        b"".join(path.read_bytes() for path in source_attempt_paths)
    ).hexdigest()
    source_manifest = source_store.read_manifest()
    target_store = EvaluationArtifactStore.create(
        root=target_dir.parent,
        run_id=target_dir.name,
        manifest={
            "run_id": target_dir.name,
            "artifact_kind": "saved-response-deterministic-replay",
            "source_run_dir": str(run_dir),
            "source_run_id": source_manifest.get("run_id"),
            "source_rubric_version": source_manifest.get("rubric_version"),
            "source_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
            "source_attempts_sha256": source_attempt_hash,
            "dataset_path": str(dataset_path),
            "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "target_attempts": len(source_attempt_paths),
            "provider_invocations": 0,
            "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
            "rubric_sha256": REPORT_SCORING_RUBRIC_SHA256,
            "rescored_from_saved_evidence": True,
        },
    )

    replay_deltas: list[float] = []
    for normalized_path in source_attempt_paths:
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
        repeated = score_question_from_evidence(evaluation_items[0], evidence)
        left = score.score
        right = repeated.score
        replay_deltas.append(
            0.0 if left is None and right is None else abs(float(left) - float(right))
        )
        attempt["score"] = score.score
        attempt["applicable_dimensions"] = score.applicable_dimensions
        attempt["expected_score_range"] = list(case.expected_score_range)
        attempt["language"] = _answer_language(case.answer)
        attempt["question_type"] = case.question_kind
        attempt["evaluation_status"] = score.evaluation_status
        attempt["evaluation_reason_code"] = score.evaluation_reason_code
        target_store.write_attempt(case.case_id, attempt["run_number"], normalized=attempt)

    attempts = target_store.load_normalized_attempts()
    manifest = target_store.read_manifest()
    expected_attempts = int(manifest["target_attempts"])
    metrics = calculate_metrics(
        attempts,
        expected_attempt_count=expected_attempts,
    ).model_dump(mode="json")
    metrics["saved_response_replay_delta"] = max(replay_deltas, default=0.0)
    metrics["provider_invocations"] = 0
    target_store.write_metrics(metrics)
    manifest["decision"] = metrics.get("decision", "PASS" if metrics["passed"] else "FAIL")
    manifest["saved_response_replay_delta"] = max(replay_deltas, default=0.0)
    target_store.write_manifest(manifest)
    return metrics


def _answer_language(value: str) -> str:
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in value)
    has_latin = any("a" <= char.lower() <= "z" for char in value)
    if has_chinese and has_latin:
        return "mixed"
    if has_chinese:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"
