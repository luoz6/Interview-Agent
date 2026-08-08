from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.services.report_semantic_dataset import (
    append_semantic_review_evidence,
    empty_semantic_review_evidence_ledger,
    load_t49_semantic_dataset_manifest,
    validate_t49_semantic_dataset,
)
from app.services.report_semantic_review import (
    build_blinded_review_artifacts,
    canonical_sha256,
    disabled_offline_judge_config,
    empty_human_review_sheet,
    evaluate_semantic_review_gate,
    load_semantic_review_dataset,
)


DEFAULT_DATASET = Path("tests/fixtures/report_semantic_blind_test_v1.json")
DEFAULT_MANIFEST = Path(
    "tests/fixtures/report_semantic_blind_test_manifest_v1.json"
)
DEFAULT_GATE_CONFIG = Path("config/interview_quality_v1_gate.json")
DEFAULT_OUTPUT_DIR = Path(
    "reports/interview-quality-v1/t49-blind-review-v1"
)
FROZEN_RANDOMIZATION_SEED = (
    "t49-semantic-review-frozen-seed-2026-08-06"
)
INITIAL_EVIDENCE_ENTRY_ID = "t49-not-run-initial"
INITIAL_EVIDENCE_RECORDED_AT = "2026-08-06T00:00:00Z"


def _json_payload(value: BaseModel | dict[str, Any]) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _write_exclusive(path: Path, value: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json_payload(value))


def generate_artifacts(
    *,
    dataset_path: Path,
    manifest_path: Path,
    gate_config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite existing T49 evidence directory: {output_dir}"
        )

    dataset = load_semantic_review_dataset(dataset_path)
    manifest = load_t49_semantic_dataset_manifest(manifest_path)
    validation = validate_t49_semantic_dataset(
        dataset=dataset,
        dataset_path=dataset_path,
        manifest=manifest,
        gate_config_path=gate_config_path,
    )
    if validation.status != "PASS":
        raise ValueError(
            "T49 dataset/manifest validation failed: "
            + ",".join(validation.issue_codes)
        )

    artifacts = build_blinded_review_artifacts(
        dataset,
        randomization_seed=FROZEN_RANDOMIZATION_SEED,
    )
    review_sheet = empty_human_review_sheet(artifacts.packet)
    gate_result = evaluate_semantic_review_gate(
        source_dataset=dataset,
        packet=artifacts.packet,
        assignment_key=artifacts.assignment_key,
        review_sheet=review_sheet,
        judge_config=disabled_offline_judge_config(artifacts.packet),
    )
    if (
        gate_result.quality_status
        != "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN"
        or gate_result.human_review_status != "NOT_RUN"
        or gate_result.provider_calls != 0
    ):
        raise ValueError("empty T49 review must remain truthfully NOT_RUN/BLOCKED")

    packet_sha256 = canonical_sha256(artifacts.packet)
    ledger = append_semantic_review_evidence(
        empty_semantic_review_evidence_ledger(
            dataset_id=dataset.dataset_id,
            packet_sha256=packet_sha256,
        ),
        entry_id=INITIAL_EVIDENCE_ENTRY_ID,
        recorded_at=INITIAL_EVIDENCE_RECORDED_AT,
        review_sheet=review_sheet,
        gate_result=gate_result,
    )

    paths = {
        "reviewer_packet": output_dir / "reviewer" / "packet.json",
        "empty_review_sheet": (
            output_dir / "reviewer" / "empty-review-sheet.json"
        ),
        "assignment_key": (
            output_dir / "coordinator-only" / "assignment-key.json"
        ),
        "evidence_ledger": output_dir / "evidence-ledger.json",
        "dataset_validation": output_dir / "dataset-validation.json",
    }
    values: dict[str, BaseModel] = {
        "reviewer_packet": artifacts.packet,
        "empty_review_sheet": review_sheet,
        "assignment_key": artifacts.assignment_key,
        "evidence_ledger": ledger,
        "dataset_validation": validation,
    }
    for name, path in paths.items():
        _write_exclusive(path, values[name])

    return {
        "status": "PASS",
        "dataset_id": dataset.dataset_id,
        "sample_size": validation.sample_size,
        "critical_case_count": validation.critical_case_count,
        "packet_sha256": packet_sha256,
        "quality_status": gate_result.quality_status,
        "human_review_status": gate_result.human_review_status,
        "provider_calls": gate_result.provider_calls,
        "output_paths": {
            name: path.as_posix() for name, path in paths.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the frozen T49 blind-review handoff artifacts."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--gate-config", type=Path, default=DEFAULT_GATE_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    result = generate_artifacts(
        dataset_path=args.dataset,
        manifest_path=args.manifest,
        gate_config_path=args.gate_config,
        output_dir=args.output_dir,
    )
    print(_json_payload(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
