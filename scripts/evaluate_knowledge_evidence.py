from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_evidence_eval import (
    EvidenceEvalIdentity,
    EvidenceEvalObservation,
    build_evidence_eval_artifact,
    build_evidence_observation_batch,
    build_evidence_threshold_registration,
    compare_evidence_eval_artifacts,
    load_evidence_calibration_dataset,
    load_evidence_eval_artifact,
    load_evidence_observation_batch,
    load_evidence_threshold_registration,
    write_evidence_eval_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and compare privacy-safe Evidence Calibration Eval artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--dataset", type=Path, required=True)

    template = subparsers.add_parser("template")
    template.add_argument("--output", type=Path, required=True)

    batch = subparsers.add_parser("batch")
    batch.add_argument("--dataset", type=Path, required=True)
    batch.add_argument("--observations", type=Path, required=True)
    batch.add_argument("--identity", type=Path, required=True)
    batch.add_argument("--split", choices=("tuning", "holdout"), required=True)
    batch.add_argument("--role", choices=("baseline", "candidate"), required=True)
    batch.add_argument("--output", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--batch", type=Path, required=True)
    run.add_argument("--thresholds", type=Path)
    run.add_argument("--output", type=Path, required=True)

    register = subparsers.add_parser("register-thresholds")
    register.add_argument("--baseline", type=Path, required=True)
    register.add_argument("--candidate-identity", type=Path, required=True)
    register.add_argument("--policy", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--thresholds", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        dataset = load_evidence_calibration_dataset(args.dataset)
        _print_json(
            {
                "status": "valid",
                "dataset_version": dataset.dataset_version,
                "dataset_sha256": dataset.dataset_sha256(),
                "case_count": len(dataset.cases),
            }
        )
        return 0
    if args.command == "template":
        _write_frozen_json(_observation_template(), args.output)
        _print_json({"output": str(args.output)})
        return 0
    if args.command == "batch":
        dataset = load_evidence_calibration_dataset(args.dataset)
        identity = EvidenceEvalIdentity.model_validate_json(
            args.identity.read_text(encoding="utf-8")
        )
        observations = tuple(
            EvidenceEvalObservation.model_validate(item)
            for item in _load_json_list(args.observations, "observations")
        )
        observation_batch = build_evidence_observation_batch(
            dataset,
            observations,
            split=args.split,
            role=args.role,
            identity=identity,
        )
        write_evidence_eval_artifact(observation_batch, args.output)
        _print_json(
            {
                "output": str(args.output),
                "batch_sha256": observation_batch.batch_sha256,
                "observation_count": len(observation_batch.observations),
            }
        )
        return 0
    if args.command == "run":
        dataset = load_evidence_calibration_dataset(args.dataset)
        observation_batch = load_evidence_observation_batch(args.batch)
        registration = (
            load_evidence_threshold_registration(args.thresholds)
            if args.thresholds is not None
            else None
        )
        artifact = build_evidence_eval_artifact(
            dataset,
            observation_batch,
            registration=registration,
        )
        write_evidence_eval_artifact(artifact, args.output)
        _print_json(
            {
                "output": str(args.output),
                "artifact_sha256": artifact.artifact_sha256,
                "observation_completeness_rate": (
                    artifact.metrics.observation_completeness_rate
                ),
            }
        )
        return 0
    if args.command == "register-thresholds":
        baseline = load_evidence_eval_artifact(args.baseline)
        identity = EvidenceEvalIdentity.model_validate_json(
            args.candidate_identity.read_text(encoding="utf-8")
        )
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        registration = build_evidence_threshold_registration(
            baseline,
            candidate_identity=identity,
            primary_metric=policy["primary_metric"],
            minimum_deltas=policy["minimum_deltas"],
            maximum_deltas=policy["maximum_deltas"],
            absolute_minimums=policy["absolute_minimums"],
            absolute_maximums=policy["absolute_maximums"],
            rationale_record_sha256=policy["rationale_record_sha256"],
        )
        write_evidence_eval_artifact(registration, args.output)
        _print_json(
            {
                "output": str(args.output),
                "registration_sha256": registration.registration_sha256,
            }
        )
        return 0
    if args.command == "compare":
        baseline = load_evidence_eval_artifact(args.baseline)
        candidate = load_evidence_eval_artifact(args.candidate)
        registration = (
            load_evidence_threshold_registration(args.thresholds)
            if args.thresholds is not None
            else None
        )
        paired = compare_evidence_eval_artifacts(
            baseline,
            candidate,
            registration=registration,
        )
        write_evidence_eval_artifact(paired, args.output)
        _print_json(
            {
                "output": str(args.output),
                "artifact_sha256": paired.artifact_sha256,
                "thresholds_passed": paired.thresholds_passed,
                "failed_thresholds": paired.failed_thresholds,
            }
        )
        return 0
    raise AssertionError("unreachable")


def _observation_template() -> dict:
    return {
        "human_calibration_labels_required": True,
        "do_not_derive_gold_labels_from_engine_output": True,
        "observations": [],
        "observation_contract": {
            "case_id": "REQUIRED",
            "candidate_evidence_ids": [],
            "selected_evidence_ids": [],
            "supplemental_evidence_ids": [],
            "final_evidence_ids": [],
            "replayed_evidence_ids": [],
            "covered_signal_sha256s": [],
            "availability": "available|degraded|unavailable",
            "sufficiency": (
                "sufficient|weak|insufficient|empty|not_evaluated"
            ),
            "reason_codes": [],
        },
    }


def _load_json_list(path: Path, label: str) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{label} must be a JSON list")
    return payload


def _write_frozen_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite frozen file: {path}") from exc


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
