from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.report_calibration_dataset import load_calibration_dataset
from app.services.report_calibration_runner import evaluate_calibration_dataset
from app.services.report_semantic_dataset import (
    load_t49_semantic_dataset_manifest,
    validate_t49_semantic_dataset,
)
from app.services.report_semantic_review import load_semantic_review_dataset
from scripts.evaluate_followup_quality import main as followup_main
from scripts.evaluate_initial_question_quality import main as initial_main


DATASET_DIR = ROOT / "tests/golden/interview_quality_v1"
DATASET_MANIFEST = DATASET_DIR / "manifest.json"
CURRENT_QUALITY_DATASETS = (
    "initial-question-quality-v2.json",
    "followup-decision-quality-v2.json",
    "report-score-quality-v2.json",
    "report-semantic-quality-v1.json",
)
CALIBRATION_DATASET = DATASET_DIR / "report-score-calibration-v1.json"
SEMANTIC_DATASET = ROOT / "tests/fixtures/report_semantic_blind_test_v1.json"
SEMANTIC_MANIFEST = (
    ROOT / "tests/fixtures/report_semantic_blind_test_manifest_v1.json"
)
GATE_CONFIG = ROOT / "config/interview_quality_v1_gate.json"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9_.-]+$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_offline_cli(main, argv: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = int(main(argv))
    return exit_code, output.getvalue()


def _validate_dataset_manifest() -> dict[str, str]:
    manifest = _read_json(DATASET_MANIFEST)
    hashes = manifest.get("files", {})
    result: dict[str, str] = {}
    for name in CURRENT_QUALITY_DATASETS:
        path = DATASET_DIR / name
        digest = _sha256(path)
        if hashes.get(name) != digest:
            raise RuntimeError(f"frozen quality dataset hash drifted: {name}")
        result[name] = digest
    return result


def run_quality_replays(*, out: Path, run_id: str) -> dict:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise RuntimeError("run_id contains unsafe characters")
    run_root = out.resolve() / run_id
    if run_root.exists() and any(run_root.iterdir()):
        raise RuntimeError("T64 quality replay run directory already exists")
    run_root.mkdir(parents=True, exist_ok=True)
    dataset_hashes = _validate_dataset_manifest()

    initial_code, initial_stdout = _run_offline_cli(
        initial_main,
        [
            "--mode",
            "fixture-replay",
            "--scope",
            "full",
            "--purpose",
            "evaluation",
            "--partition",
            "all",
            "--out",
            str(run_root),
            "--run-id",
            "initial-question",
        ],
    )
    initial_manifest = _read_json(run_root / "initial-question/manifest.json")
    initial_metrics = _read_json(run_root / "initial-question/metrics.json")
    if not (
        initial_code == 2
        and initial_manifest.get("decision") == "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
        and initial_manifest.get("provider_called") is False
        and initial_manifest.get("provider_invocations_this_run") == 0
        and initial_metrics.get("automated_status") == "PASS"
    ):
        raise RuntimeError("initial-question offline replay failed closed checks")

    followup_code, followup_stdout = _run_offline_cli(
        followup_main,
        [
            "--mode",
            "fixture-replay",
            "--scope",
            "full",
            "--purpose",
            "evaluation",
            "--partition",
            "all",
            "--out",
            str(run_root),
            "--run-id",
            "followup-decision",
        ],
    )
    followup_manifest = _read_json(run_root / "followup-decision/manifest.json")
    followup_metrics = _read_json(run_root / "followup-decision/metrics.json")
    if not (
        followup_code == 2
        and followup_manifest.get("provider_called") is False
        and followup_manifest.get("provider_invocations_this_run") == 0
        and followup_metrics.get("automated_status") == "PASS"
        and followup_metrics.get("quality_status")
        == "BLOCKED_PENDING_INDEPENDENT_REVIEW"
    ):
        raise RuntimeError("follow-up offline replay failed closed checks")

    calibration = load_calibration_dataset(CALIBRATION_DATASET)
    scoring = evaluate_calibration_dataset(
        calibration,
        partition="dev",
        allow_unreviewed_dev=True,
    )
    if not (
        scoring.provider_invocations == 0
        and scoring.metrics.completed_attempt_count == 60
        and scoring.metrics.expected_range_attempt_hit_rate == 1.0
        and scoring.error_categories == {}
    ):
        raise RuntimeError("report-score offline replay failed")

    semantic_dataset = load_semantic_review_dataset(SEMANTIC_DATASET)
    semantic_manifest = load_t49_semantic_dataset_manifest(SEMANTIC_MANIFEST)
    semantic = validate_t49_semantic_dataset(
        dataset=semantic_dataset,
        dataset_path=SEMANTIC_DATASET,
        manifest=semantic_manifest,
        gate_config_path=GATE_CONFIG,
    )
    if semantic.status != "PASS" or semantic.issue_codes:
        raise RuntimeError("report-semantic offline replay failed")

    result = {
        "schema_version": "interview-quality-v1-t64-quality-replays-v1",
        "run_id": run_id,
        "status": "PASS_ENGINEERING_QUALITY_BLOCKED",
        "engineering_status": "PASS",
        "quality_status": "BLOCKED",
        "provider_called": False,
        "provider_calls": 0,
        "dataset_hashes": dataset_hashes,
        "replays": {
            "initial_question": {
                "engineering_status": "PASS",
                "quality_status": "BLOCKED_SYNTHETIC_FIXTURE_ONLY",
                "case_count": initial_metrics["attempt_count"],
                "provider_calls": 0,
                "cli_exit_code": initial_code,
                "stdout_sha256": hashlib.sha256(
                    initial_stdout.encode("utf-8")
                ).hexdigest(),
            },
            "followup_decision": {
                "engineering_status": "PASS",
                "quality_status": "BLOCKED_PENDING_INDEPENDENT_REVIEW",
                "case_count": followup_metrics["dataset_case_count"],
                "sequence_count": followup_metrics["sequence_replay"][
                    "sequence_count"
                ],
                "provider_calls": 0,
                "cli_exit_code": followup_code,
                "stdout_sha256": hashlib.sha256(
                    followup_stdout.encode("utf-8")
                ).hexdigest(),
            },
            "report_score": {
                "engineering_status": "PASS",
                "quality_status": "BLOCKED_PENDING_INDEPENDENT_REVIEW",
                "case_count": scoring.metrics.completed_attempt_count,
                "expected_range_attempt_hit_rate": (
                    scoring.metrics.expected_range_attempt_hit_rate
                ),
                "provider_calls": scoring.provider_invocations,
                "rubric_version": scoring.rubric_version,
                "rubric_sha256": scoring.rubric_sha256,
            },
            "report_semantic": {
                "engineering_status": "PASS",
                "quality_status": "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN",
                "case_count": semantic.sample_size,
                "critical_case_count": semantic.critical_case_count,
                "covered_scenario_count": len(semantic.covered_scenarios),
                "provider_calls": 0,
            },
        },
    }
    result_path = run_root / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all four T64 offline quality replays")
    parser.add_argument("--out", type=Path, default=Path("tmp/t64/quality-replays"))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_quality_replays(out=args.out, run_id=args.run_id)
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "interview-quality-v1-t64-quality-replays-v1",
                    "status": "FAIL",
                    "detail": str(exc),
                    "provider_calls": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
