from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_eval_dataset_v2 import EVALUATION_GROUP_DOMAIN_MAP
from app.services.knowledge_eval_dataset_v3 import CaseType


DEFAULT_MANIFEST = ROOT / "app/data/knowledge_v2/manifest.json"
DEFAULT_OUTPUT = ROOT / "eval/knowledge-v3/authoring"
PACKAGE_VERSION = "knowledge-eval-v3-authoring-rmqv4-2026-08-13-v1"
PROTOCOL_VERSION = "knowledge-eval-v3-annotation-protocol-2026-08-13-v1"

CASE_TYPES = (
    "exact_technical_term",
    "alias_only",
    "acronym",
    "semantic_paraphrase",
    "chinese_paraphrase",
    "weak_keyword",
    "multi_topic",
    "ambiguous",
    "hard_negative",
    "out_of_domain",
    "no_evidence",
    "cross_domain_confusion",
    "metadata_routing_error",
    "filter_boundary",
)

if CASE_TYPES != CaseType.__args__:
    raise RuntimeError("annotation package case types drifted from the V3 contract")

CASE_TYPE_QUOTAS = {
    "exact_technical_term": {"tuning": 6, "holdout": 2},
    "alias_only": {"tuning": 6, "holdout": 2},
    "acronym": {"tuning": 4, "holdout": 2},
    "semantic_paraphrase": {"tuning": 6, "holdout": 2},
    "chinese_paraphrase": {"tuning": 6, "holdout": 2},
    "weak_keyword": {"tuning": 5, "holdout": 2},
    "multi_topic": {"tuning": 5, "holdout": 2},
    "ambiguous": {"tuning": 5, "holdout": 2},
    "hard_negative": {"tuning": 6, "holdout": 2},
    "out_of_domain": {"tuning": 4, "holdout": 1},
    "no_evidence": {"tuning": 4, "holdout": 1},
    "cross_domain_confusion": {"tuning": 6, "holdout": 2},
    "metadata_routing_error": {"tuning": 6, "holdout": 1},
    "filter_boundary": {"tuning": 6, "holdout": 2},
}

GROUP_QUOTAS = {
    "fastapi": {"tuning": 13, "holdout": 4},
    "redis": {"tuning": 12, "holdout": 4},
    "relational-database": {"tuning": 14, "holdout": 4},
    "rocketmq": {"tuning": 13, "holdout": 4},
    "system-design": {"tuning": 12, "holdout": 4},
    "reliability": {"tuning": 11, "holdout": 5},
}

GROUP_DOMAIN_OPTIONS = {
    "fastapi": ("fastapi",),
    "redis": ("redis",),
    "relational-database": ("mysql", "postgresql"),
    "rocketmq": ("rocketmq",),
    "system-design": ("system-design",),
    "reliability": ("system-design", "postgresql"),
}

if set(GROUP_DOMAIN_OPTIONS) != set(EVALUATION_GROUP_DOMAIN_MAP):
    raise RuntimeError("annotation package groups drifted from the V3 contract")
if any(
    not set(domains) <= EVALUATION_GROUP_DOMAIN_MAP[group]
    for group, domains in GROUP_DOMAIN_OPTIONS.items()
):
    raise RuntimeError("annotation package domains violate the V3 group mapping")

GENERATED_FILES = (
    "case-quota.json",
    "tuning-authoring-template.jsonl",
    "holdout-authoring-template.jsonl",
    "family-isolation-map.json",
    "annotator-a-template.jsonl",
    "annotator-b-template.jsonl",
    "adjudication-template.jsonl",
    "chunk-catalog.json",
)


def canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _round_robin_schedule(quotas: dict[str, int], order: Iterable[str]) -> list[str]:
    remaining = dict(quotas)
    scheduled: list[str] = []
    while any(remaining.values()):
        for name in order:
            if remaining.get(name, 0) > 0:
                scheduled.append(name)
                remaining[name] -= 1
    return scheduled


def build_slots() -> list[dict]:
    slots: list[dict] = []
    for split, expected_count in (("tuning", 75), ("holdout", 25)):
        case_types = _round_robin_schedule(
            {name: CASE_TYPE_QUOTAS[name][split] for name in CASE_TYPES},
            CASE_TYPES,
        )
        groups = _round_robin_schedule(
            {name: GROUP_QUOTAS[name][split] for name in GROUP_QUOTAS},
            GROUP_QUOTAS,
        )
        if len(case_types) != expected_count or len(groups) != expected_count:
            raise ValueError(f"invalid {split} quota total")
        for index, (case_type, group) in enumerate(zip(case_types, groups), 1):
            slot_id = f"kev3-{split}-{index:03d}"
            slots.append(
                {
                    "schema_version": "knowledge-eval-v3-authoring-slot-v1",
                    "slot_id": slot_id,
                    "case_id": slot_id,
                    "case_family": f"family-{slot_id}",
                    "split": split,
                    "planned_case_type": case_type,
                    "planned_evaluation_group": group,
                    "allowed_domain_options": list(GROUP_DOMAIN_OPTIONS[group]),
                    "query_text": None,
                    "canonical_tags": None,
                    "source_types": None,
                    "allowed_domains": None,
                    "primary_relevant_chunk_ids": None,
                    "accepted_related_chunk_ids": None,
                    "excluded_chunk_ids": None,
                    "expected_no_evidence": None,
                    "top_k": 5,
                    "authoring_status": "blank",
                }
            )
    return slots


def _annotation_row(slot: dict, annotator: str) -> dict:
    return {
        "schema_version": "knowledge-eval-v3-independent-annotation-v1",
        "slot_id": slot["slot_id"],
        "case_id": slot["case_id"],
        "annotator_position": annotator,
        "annotator_identity_sha256": None,
        "implementation_output_blinded": True,
        "started_at": None,
        "completed_at": None,
        "primary_relevant_chunk_ids": None,
        "accepted_related_chunk_ids": None,
        "excluded_chunk_ids": None,
        "expected_no_evidence": None,
        "annotation_record_sha256": None,
        "annotation_status": "blank",
    }


def _adjudication_row(slot: dict) -> dict:
    return {
        "schema_version": "knowledge-eval-v3-adjudication-v1",
        "slot_id": slot["slot_id"],
        "case_id": slot["case_id"],
        "annotator_a_record_sha256": None,
        "annotator_b_record_sha256": None,
        "consensus_primary_relevant_chunk_ids": None,
        "consensus_accepted_related_chunk_ids": None,
        "consensus_excluded_chunk_ids": None,
        "consensus_expected_no_evidence": None,
        "label_consensus_record_sha256": None,
        "adjudicator_identity_sha256": None,
        "adjudication_status": "blank",
    }


def _write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            stream.write("\n")


def _privacy_safe_catalog(manifest: dict) -> dict:
    fields = (
        "chunk_id",
        "title",
        "domain",
        "source_type",
        "tags",
        "aliases",
        "technical_terms",
        "content_kind",
    )
    return {
        "schema_version": "knowledge-eval-v3-authoring-chunk-catalog-v1",
        "corpus_version": manifest["corpus_version"],
        "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
        "chunks": [
            {field: chunk[field] for field in fields if field in chunk}
            for chunk in manifest["chunks"]
        ],
    }


def build_package(
    output_dir: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    baseline_revision: str,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    permitted_existing = {"README.md", "annotation-protocol.md"}
    existing = {path.name for path in output_dir.iterdir()}
    unexpected = sorted(existing - permitted_existing)
    if unexpected:
        raise FileExistsError(
            "refusing to overwrite annotation package files: " + ", ".join(unexpected)
        )
    instruction_files = ("README.md", "annotation-protocol.md")
    missing = [name for name in instruction_files if not (output_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            "instruction files must exist before package generation: " + ", ".join(missing)
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    slots = build_slots()
    tuning = [slot for slot in slots if slot["split"] == "tuning"]
    holdout = [slot for slot in slots if slot["split"] == "holdout"]

    case_quota = {
        "schema_version": "knowledge-eval-v3-case-quota-v1",
        "package_version": PACKAGE_VERSION,
        "total_slots": 100,
        "split_quotas": {"tuning": 75, "holdout": 25},
        "case_type_quotas": CASE_TYPE_QUOTAS,
        "evaluation_group_quotas": GROUP_QUOTAS,
        "minimum_cases_per_case_type": 3,
        "family_assignment": "one pre-frozen unique family per slot",
    }
    _write_json(output_dir / "case-quota.json", case_quota)
    _write_jsonl(output_dir / "tuning-authoring-template.jsonl", tuning)
    _write_jsonl(output_dir / "holdout-authoring-template.jsonl", holdout)
    _write_json(
        output_dir / "family-isolation-map.json",
        {
            "schema_version": "knowledge-eval-v3-family-isolation-map-v1",
            "split_frozen": True,
            "families": [
                {
                    "slot_id": slot["slot_id"],
                    "case_id": slot["case_id"],
                    "case_family": slot["case_family"],
                    "split": slot["split"],
                    "holdout_access": (
                        "sealed_holdout_owner_only"
                        if slot["split"] == "holdout"
                        else "tuning_annotation_team"
                    ),
                }
                for slot in slots
            ],
        },
    )
    _write_jsonl(
        output_dir / "annotator-a-template.jsonl",
        (_annotation_row(slot, "A") for slot in slots),
    )
    _write_jsonl(
        output_dir / "annotator-b-template.jsonl",
        (_annotation_row(slot, "B") for slot in slots),
    )
    _write_jsonl(
        output_dir / "adjudication-template.jsonl",
        (_adjudication_row(slot) for slot in slots),
    )
    _write_json(output_dir / "chunk-catalog.json", _privacy_safe_catalog(manifest))

    file_hashes = {
        name: file_sha256(output_dir / name)
        for name in (*instruction_files, *GENERATED_FILES)
    }
    package_manifest_without_hash = {
        "schema_version": "knowledge-eval-v3-authoring-package-manifest-v1",
        "package_version": PACKAGE_VERSION,
        "package_status": "blank_authoring_scaffold",
        "runnable_v3_dataset": False,
        "independent_evaluation_complete": False,
        "baseline_revision": baseline_revision,
        "corpus_version": manifest["corpus_version"],
        "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
        "annotation_protocol_version": PROTOCOL_VERSION,
        "annotation_protocol_sha256": file_hashes["annotation-protocol.md"],
        "slot_count": 100,
        "tuning_count": 75,
        "holdout_count": 25,
        "family_count": 100,
        "case_type_count": len(CASE_TYPES),
        "minimum_annotators_per_case": 2,
        "implementation_output_blinded": True,
        "file_sha256": file_hashes,
        "historical_sources_reused": False,
        "blocked_claims": [
            "independent annotation complete",
            "inter-rater agreement measured",
            "Eval V3 dataset release-ready",
            "Hybrid approved",
            "Shadow approved",
            "Canary approved",
            "production ready",
        ],
    }
    package_manifest = {
        **package_manifest_without_hash,
        "package_manifest_sha256": canonical_sha256(package_manifest_without_hash),
    }
    _write_json(output_dir / "package-manifest.json", package_manifest)
    return validate_package(output_dir)


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path.name}:{line_number}") from exc
    return rows


def validate_package(output_dir: Path) -> dict:
    package_manifest = json.loads(
        (output_dir / "package-manifest.json").read_text(encoding="utf-8")
    )
    supplied_manifest_hash = package_manifest.pop("package_manifest_sha256")
    if canonical_sha256(package_manifest) != supplied_manifest_hash:
        raise ValueError("package manifest SHA-256 mismatch")
    for name, expected_hash in package_manifest["file_sha256"].items():
        if file_sha256(output_dir / name) != expected_hash:
            raise ValueError(f"package file SHA-256 mismatch: {name}")

    tuning = _read_jsonl(output_dir / "tuning-authoring-template.jsonl")
    holdout = _read_jsonl(output_dir / "holdout-authoring-template.jsonl")
    slots = tuning + holdout
    if len(tuning) != 75 or len(holdout) != 25 or len(slots) != 100:
        raise ValueError("annotation package must contain 75 tuning and 25 holdout slots")
    if len({slot["slot_id"] for slot in slots}) != 100:
        raise ValueError("annotation slot IDs must be unique")
    if len({slot["case_id"] for slot in slots}) != 100:
        raise ValueError("annotation case IDs must be unique")
    for slot in slots:
        if slot["query_text"] is not None or slot["authoring_status"] != "blank":
            raise ValueError("authoring slots must remain blank before human annotation")
        for field in (
            "canonical_tags",
            "source_types",
            "allowed_domains",
            "primary_relevant_chunk_ids",
            "accepted_related_chunk_ids",
            "excluded_chunk_ids",
            "expected_no_evidence",
        ):
            if slot[field] is not None:
                raise ValueError(f"authoring field must remain blank: {field}")

    type_counts = Counter(slot["planned_case_type"] for slot in slots)
    if set(type_counts) != set(CASE_TYPES) or min(type_counts.values()) < 3:
        raise ValueError("all 14 V3 case types require at least three slots")
    expected_type_counts = {
        name: sum(CASE_TYPE_QUOTAS[name].values()) for name in CASE_TYPES
    }
    if dict(type_counts) != expected_type_counts:
        raise ValueError("case-type quotas do not match the package plan")
    group_counts = Counter(
        (slot["planned_evaluation_group"], slot["split"]) for slot in slots
    )
    expected_group_counts = {
        (group, split): count
        for group, split_counts in GROUP_QUOTAS.items()
        for split, count in split_counts.items()
    }
    if dict(group_counts) != expected_group_counts:
        raise ValueError("evaluation-group quotas do not match the package plan")

    family_map = json.loads(
        (output_dir / "family-isolation-map.json").read_text(encoding="utf-8")
    )["families"]
    family_splits: dict[str, set[str]] = defaultdict(set)
    for row in family_map:
        family_splits[row["case_family"]].add(row["split"])
    if len(family_splits) != 100 or any(len(splits) != 1 for splits in family_splits.values()):
        raise ValueError("case-family leakage detected")

    slot_ids = {slot["slot_id"] for slot in slots}
    for filename in (
        "annotator-a-template.jsonl",
        "annotator-b-template.jsonl",
        "adjudication-template.jsonl",
    ):
        rows = _read_jsonl(output_dir / filename)
        if len(rows) != 100 or {row["slot_id"] for row in rows} != slot_ids:
            raise ValueError(f"incomplete template coverage: {filename}")
        if any(
            row[next(key for key in row if key.endswith("status"))] != "blank"
            for row in rows
        ):
            raise ValueError(f"template is not blank: {filename}")

    historical_ids: set[str] = set()
    for filename in (
        ROOT / "artifacts/knowledge-rag-v2/baseline/legacy-pilot-rmqv4.json",
        ROOT / "artifacts/knowledge-rag-v2/baseline/legacy-memory-p1-rmqv4.json",
    ):
        if filename.exists():
            payload = json.loads(filename.read_text(encoding="utf-8"))
            historical_ids.update(case["case_id"] for case in payload.get("cases", []))
    overlap = historical_ids & {slot["case_id"] for slot in slots}
    if overlap:
        raise ValueError("historical baseline case IDs were reused")

    return {
        "status": "valid_blank_authoring_scaffold",
        "package_version": package_manifest["package_version"],
        "slot_count": 100,
        "tuning_count": 75,
        "holdout_count": 25,
        "case_type_count": 14,
        "family_count": 100,
        "historical_case_id_overlap_count": 0,
        "runnable_v3_dataset": False,
        "independent_evaluation_complete": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or validate the blank Knowledge Eval V3 annotation package"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--baseline-revision", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--package", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    summary = (
        build_package(
            args.output,
            manifest_path=args.manifest,
            baseline_revision=args.baseline_revision,
        )
        if args.command == "build"
        else validate_package(args.package)
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
