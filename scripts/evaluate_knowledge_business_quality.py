from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_business_eval import (
    BlindBusinessEvalPackage,
    BusinessEvalTargetThresholds,
    build_blind_business_eval_package,
    build_business_eval_threshold_registration,
    compare_blind_business_eval,
    load_blind_mapping,
    load_blind_package,
    load_business_annotations,
    load_business_eval_dataset,
    load_business_threshold_registration,
    write_business_eval_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and score independent Follow-up/Reviewer blind A/B evals."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--dataset", type=Path, required=True)
    validate.add_argument("--release-shape", action="store_true")

    package = subparsers.add_parser("package")
    package.add_argument("--dataset", type=Path, required=True)
    package.add_argument("--split", choices=("tuning", "holdout"), required=True)
    package.add_argument("--seed", required=True)
    package.add_argument("--package-output", type=Path, required=True)
    package.add_argument("--mapping-output", type=Path, required=True)

    template = subparsers.add_parser("annotation-template")
    template.add_argument("--package", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)

    register = subparsers.add_parser("register-thresholds")
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--package", type=Path, required=True)
    register.add_argument("--mapping", type=Path, required=True)
    register.add_argument("--thresholds", type=Path, required=True)
    register.add_argument("--rationale-record-sha256", required=True)
    register.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--dataset", type=Path, required=True)
    compare.add_argument("--package", type=Path, required=True)
    compare.add_argument("--mapping", type=Path, required=True)
    compare.add_argument("--annotations", type=Path, required=True)
    compare.add_argument("--registration", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        dataset = load_business_eval_dataset(args.dataset)
        if args.release_shape:
            dataset.validate_release_shape()
        _print_json(
            {
                "valid": True,
                "dataset_version": dataset.dataset_version,
                "dataset_sha256": dataset.dataset_sha256(),
                "case_count": len(dataset.cases),
            }
        )
        return 0
    if args.command == "package":
        dataset = load_business_eval_dataset(args.dataset)
        package, mapping = build_blind_business_eval_package(
            dataset,
            split=args.split,
            seed=args.seed,
        )
        write_business_eval_artifact(package, args.package_output)
        write_business_eval_artifact(mapping, args.mapping_output)
        _print_json(
            {
                "package_sha256": package.package_sha256,
                "mapping_sha256": mapping.mapping_sha256,
                "case_count": len(package.cases),
            }
        )
        return 0
    if args.command == "annotation-template":
        package = load_blind_package(args.package)
        payload = _annotation_template(package)
        _write_new_json(args.output, payload)
        _print_json({"output": str(args.output), "case_count": len(package.cases)})
        return 0
    if args.command == "register-thresholds":
        dataset = load_business_eval_dataset(args.dataset)
        package = load_blind_package(args.package)
        mapping = load_blind_mapping(args.mapping)
        threshold_payload = json.loads(args.thresholds.read_text(encoding="utf-8"))
        thresholds = {
            target: BusinessEvalTargetThresholds.model_validate(value)
            for target, value in threshold_payload.items()
        }
        registration = build_business_eval_threshold_registration(
            dataset,
            package,
            mapping,
            target_thresholds=thresholds,
            rationale_record_sha256=args.rationale_record_sha256,
        )
        write_business_eval_artifact(registration, args.output)
        _print_json({"registration_sha256": registration.registration_sha256})
        return 0
    if args.command == "compare":
        dataset = load_business_eval_dataset(args.dataset)
        package = load_blind_package(args.package)
        mapping = load_blind_mapping(args.mapping)
        annotations = load_business_annotations(args.annotations)
        registration = (
            load_business_threshold_registration(args.registration)
            if args.registration
            else None
        )
        result = compare_blind_business_eval(
            dataset,
            package,
            mapping,
            annotations,
            registration=registration,
        )
        write_business_eval_artifact(result, args.output)
        _print_json(
            {
                "artifact_sha256": result.artifact_sha256,
                "thresholds_passed": result.thresholds_passed,
                "failed_thresholds": result.failed_thresholds,
            }
        )
        return 0
    raise AssertionError("unreachable")


def _annotation_template(package: BlindBusinessEvalPackage) -> dict:
    return {
        "schema_version": "knowledge-business-annotations-v1",
        "dataset_sha256": package.dataset_sha256,
        "package_sha256": package.package_sha256,
        "split": package.split,
        "governance": {
            "protocol_version": "REPLACE_WITH_APPROVED_PROTOCOL",
            "annotator_roles": ["REPLACE_WITH_INDEPENDENT_ANNOTATOR_ROLE"],
            "minimum_qualification": "REPLACE_WITH_MINIMUM_QUALIFICATION",
            "minimum_annotators_per_case": 2,
            "blinded": True,
            "adjudication_rule": "REPLACE_WITH_APPROVED_ADJUDICATION_RULE",
            "agreement_metric": "REPLACE_WITH_PREREGISTERED_METRIC",
            "agreement_value": None,
            "minimum_agreement": None,
            "collection_started_at": None,
            "collection_completed_at": None,
        },
        "records": [],
        "consensus": [],
        "instructions": {
            "human_annotations_required": True,
            "do_not_use_engine_labels": True,
            "case_ids": [case.case_id for case in package.cases],
            "dimensions_by_target": {
                "followup": [
                    "answer_specificity",
                    "missing_or_incorrect_signal_targeting",
                    "depth_gain",
                    "role_seniority_relevance",
                    "evidence_grounding",
                    "repetition",
                    "over_leading",
                    "unsupported_technical_claim",
                ],
                "reviewer": [
                    "expert_agreement",
                    "score_stability",
                    "evidence_support",
                    "confidence_calibration",
                    "no_evidence_handling",
                    "system_failure_handling",
                    "unsupported_judgment",
                    "repeated_evaluation_variance",
                ],
            },
        },
    }


def _write_new_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
