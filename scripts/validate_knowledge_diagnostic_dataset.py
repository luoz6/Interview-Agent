from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_eval_artifacts_v3 import canonical_sha256
from app.services.knowledge_eval_dataset_v3 import (
    CaseType,
    load_knowledge_retrieval_dataset_v3,
)


DEFAULT_MANIFEST = Path("app/data/knowledge_v2/manifest.json")
DEFAULT_DIAGNOSTIC_DIR = Path("eval/knowledge-v3/machine-preannotation")


def validate_diagnostic_dataset(
    dataset_path: Path,
    provenance_path: Path,
    manifest_path: Path,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset = load_knowledge_retrieval_dataset_v3(
        dataset_path,
        manifest=manifest,
        require_diagnostic_integrity=True,
    )
    provenance_payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance = dict(provenance_payload)
    supplied_provenance_hash = provenance.pop("provenance_sha256")
    if canonical_sha256(provenance) != supplied_provenance_hash:
        raise ValueError("diagnostic provenance SHA-256 mismatch")
    if provenance["dataset_canonical_sha256"] != canonical_sha256(dataset_payload):
        raise ValueError("diagnostic dataset SHA-256 mismatch")
    if dataset_payload.get("governance") is not None:
        raise ValueError("machine-assisted diagnostic dataset cannot claim governance")
    if len(dataset.cases) != 100:
        raise ValueError("diagnostic dataset requires exactly 100 cases")

    split_counts = Counter(case.split for case in dataset.cases)
    if split_counts != {"tuning": 75, "holdout": 25}:
        raise ValueError("diagnostic split must be 75 tuning and 25 diagnostic holdout")
    type_counts = Counter(case.case_type for case in dataset.cases)
    if set(type_counts) != set(CaseType.__args__):
        raise ValueError("diagnostic dataset must cover every V3 case type")

    family_splits: defaultdict[str, set[str]] = defaultdict(set)
    raw_cases = {
        item["case_id"]: item for item in dataset_payload.get("cases", [])
    }
    for case in dataset.cases:
        family_splits[case.case_family].add(case.split)
        raw_case = raw_cases[case.case_id]
        if raw_case.get("annotator_identity_sha256s") or raw_case.get(
            "annotation_record_sha256s"
        ):
            raise ValueError("machine-assisted diagnostics cannot claim human records")
        if raw_case.get("label_consensus_record_sha256") is not None:
            raise ValueError("machine-assisted diagnostics cannot claim consensus")
    if len(family_splits) != 100 or any(
        len(splits) != 1 for splits in family_splits.values()
    ):
        raise ValueError("diagnostic case-family leakage detected")

    provenance_splits: defaultdict[str, set[str]] = defaultdict(set)
    split_by_case = {case.case_id: case.split for case in dataset.cases}
    for row in provenance["cases"]:
        provenance_splits[row["semantic_family_key"]].add(
            split_by_case[row["case_id"]]
        )
    if len(provenance_splits) != 100 or any(
        len(splits) != 1 for splits in provenance_splits.values()
    ):
        raise ValueError("diagnostic semantic-family leakage detected")
    if provenance["human_annotator_count"] != 0:
        raise ValueError("machine-assisted diagnostics cannot claim human annotators")
    if provenance["eligible_as_independent_eval_evidence"]:
        raise ValueError("diagnostic dataset cannot claim independent evidence")

    return {
        "status": "valid_demo_diagnostic_dataset",
        "dataset_version": dataset.version,
        "dataset_canonical_sha256": provenance["dataset_canonical_sha256"],
        "provenance_sha256": supplied_provenance_hash,
        "case_count": len(dataset.cases),
        "tuning_count": split_counts["tuning"],
        "diagnostic_holdout_count": split_counts["holdout"],
        "case_type_count": len(type_counts),
        "family_count": len(family_splits),
        "no_evidence_count": len(dataset.no_evidence_cases()),
        "evidence_case_count": len(dataset.evidence_cases()),
        "curation": "Curated / Machine-assisted",
        "production_claim": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the frozen Demo Diagnostic Dataset and provenance."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DIAGNOSTIC_DIR / "dataset.json",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=DEFAULT_DIAGNOSTIC_DIR / "provenance.json",
    )
    args = parser.parse_args(argv)
    summary = validate_diagnostic_dataset(
        args.dataset,
        args.provenance,
        args.manifest,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
