from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.adapters.knowledge.pilot_unit_resolver import PilotKnowledgeUnitResolver
from app.runtime.config.loader import load_knowledge_runtime_settings
from app.services.knowledge_eval_dataset_v2 import (
    load_knowledge_retrieval_dataset_v2,
)
from scripts.build_knowledge_manifest_v2 import (
    DEFAULT_CORPUS_VERSION,
    DEFAULT_OUTPUT_PATH,
    KNOWLEDGE_V2_ROOT,
    build_manifest_v2,
)
from scripts.load_knowledge_v2 import build_chunks_v2


PILOT_DATASET_PATH = Path("tests/golden/knowledge_retrieval_v2_pilot.json")
MEMORY_P1_DATASET_PATH = Path("tests/golden/knowledge_retrieval_memory_p1.json")
LEGACY_MANIFEST_PATH = Path("app/data/knowledge/manifest.json")
EXPECTED_CORPUS_HASH = (
    "deb709817c6ea1ac89db8f0452f1183d0168952d5d568e08b704869c90555e84"
)
EXPECTED_LEGACY_HASH = (
    "ad0948a6dc15af835247eb95b8fad4a069bece865779060e19c708f829eb9320"
)
EXPECTED_ROCKETMQ_IDS = frozenset(
    {
        "rocketmq_backend",
        "rocketmq_delivery",
        "rocketmq_load_balancing",
        "rocketmq_operations",
        "rocketmq_retry_dead_letter",
    }
)
EXTERNAL_BLOCKERS = (
    "AUTHORIZED_PGVECTOR_LOAD_REQUIRED",
    "INDEPENDENT_EVAL_V3_DATASET_REQUIRED",
    "HOLDOUT_PROMOTION_EVIDENCE_REQUIRED",
    "PRIVACY_AND_PRODUCTION_ROLLOUT_APPROVAL_REQUIRED",
)


def _read_json(path: Path | str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _referenced_ids(dataset) -> set[str]:
    return {
        chunk_id
        for case in dataset.cases
        for chunk_id in (
            case.primary_relevant_chunk_ids
            + case.accepted_related_chunk_ids
            + case.excluded_chunk_ids
        )
    }


def build_rocketmq_v4_preflight(
    *,
    knowledge_root: Path | str = KNOWLEDGE_V2_ROOT,
    manifest_path: Path | str = DEFAULT_OUTPUT_PATH,
    pilot_dataset_path: Path | str = PILOT_DATASET_PATH,
    memory_p1_dataset_path: Path | str = MEMORY_P1_DATASET_PATH,
    legacy_manifest_path: Path | str = LEGACY_MANIFEST_PATH,
    runtime_environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate repository readiness without database, provider, or network access."""

    failure_reasons: list[str] = []
    committed = _read_json(manifest_path)
    rebuilt = build_manifest_v2(
        knowledge_root,
        corpus_version=DEFAULT_CORPUS_VERSION,
    )
    if committed != rebuilt:
        failure_reasons.append("ACTIVE_MANIFEST_DRIFT")
    if (
        committed.get("corpus_version") != DEFAULT_CORPUS_VERSION
        or committed.get("chunk_count") != 31
        or committed.get("corpus_manifest_sha256") != EXPECTED_CORPUS_HASH
    ):
        failure_reasons.append("ACTIVE_CORPUS_IDENTITY_MISMATCH")

    chunks = build_chunks_v2(knowledge_root, manifest=committed)
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    domain_counts = Counter(chunk.domain for chunk in chunks)
    runtime_metadata_valid = all(
        chunk.metadata.get("corpus_version") == DEFAULT_CORPUS_VERSION
        and chunk.metadata.get("corpus_manifest_sha256") == EXPECTED_CORPUS_HASH
        and chunk.metadata.get("metadata_schema_version")
        == "knowledge-metadata-v2.1"
        and bool(chunk.metadata.get("topic"))
        for chunk in chunks
    )
    if len(chunks) != 31 or not runtime_metadata_valid:
        failure_reasons.append("RUNTIME_CHUNK_CONTRACT_MISMATCH")
    if (
        domain_counts.get("rocketmq") != 5
        or domain_counts.get("kafka", 0) != 0
        or EXPECTED_ROCKETMQ_IDS - chunk_ids
        or any(chunk_id.startswith("kafka_") for chunk_id in chunk_ids)
    ):
        failure_reasons.append("ROCKETMQ_CORPUS_BOUNDARY_MISMATCH")

    coverage_tags = set(committed.get("coverage", {}).get("canonical_tags", []))
    if "rocketmq" not in coverage_tags or "kafka" in coverage_tags:
        failure_reasons.append("ROCKETMQ_COVERAGE_MISMATCH")

    pilot = load_knowledge_retrieval_dataset_v2(
        pilot_dataset_path,
        expected_case_count=12,
        manifest=committed,
    )
    memory_p1 = load_knowledge_retrieval_dataset_v2(
        memory_p1_dataset_path,
        expected_case_count=18,
        manifest=committed,
    )
    datasets_are_current = (
        pilot.version == "stage44b1-knowledge-retrieval-v2-pilot"
        and memory_p1.version == "memory-p1-knowledge-retrieval-v4"
        and _referenced_ids(pilot) <= chunk_ids
        and _referenced_ids(memory_p1) <= chunk_ids
        and all(case.evaluation_group != "kafka" for case in pilot.cases)
        and all(case.evaluation_group != "kafka" for case in memory_p1.cases)
        and all("kafka" not in case.allowed_domains for case in pilot.cases)
        and all("kafka" not in case.allowed_domains for case in memory_p1.cases)
    )
    if not datasets_are_current:
        failure_reasons.append("ACTIVE_DATASET_IDENTITY_MISMATCH")

    rocketmq_unit = PilotKnowledgeUnitResolver().resolve(
        [next(chunk for chunk in chunks if chunk.chunk_id == "rocketmq_delivery")]
    )
    if (
        rocketmq_unit is None
        or rocketmq_unit.knowledge_unit_id != "rocketmq-delivery"
        or rocketmq_unit.domain != "rocketmq"
        or rocketmq_unit.source_references != ("rocketmq_delivery",)
        or rocketmq_unit.review_status != "reviewed"
    ):
        failure_reasons.append("ROCKETMQ_PILOT_UNIT_MISMATCH")

    settings = load_knowledge_runtime_settings(
        {} if runtime_environ is None else runtime_environ
    )
    runtime_defaults_safe = (
        settings.engine == "legacy"
        and settings.hybrid_rollout_percent == 0
        and settings.shadow_enabled is False
    )
    if not runtime_defaults_safe:
        failure_reasons.append("UNSAFE_RUNTIME_DEFAULTS")

    legacy = _read_json(legacy_manifest_path)
    legacy_ids = {
        item.get("chunk_id")
        for item in legacy.get("chunks", [])
        if isinstance(item, dict)
    }
    legacy_frozen = (
        legacy.get("chunk_count") == 25
        and legacy.get("corpus_manifest_sha256") == EXPECTED_LEGACY_HASH
        and len({item for item in legacy_ids if str(item).startswith("kafka_")}) == 5
    )
    if not legacy_frozen:
        failure_reasons.append("FROZEN_V1_IDENTITY_MISMATCH")

    return {
        "schema_version": "knowledge-rocketmq-v4-preflight-v1",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "repository_ready": not failure_reasons,
        "external_release_ready": False,
        "external_blockers": list(EXTERNAL_BLOCKERS),
        "corpus": {
            "version": committed.get("corpus_version"),
            "chunk_count": committed.get("chunk_count"),
            "manifest_sha256": committed.get("corpus_manifest_sha256"),
            "manifest_reproducible": committed == rebuilt,
            "metadata_v21_count": sum(
                chunk.metadata.get("metadata_schema_version")
                == "knowledge-metadata-v2.1"
                for chunk in chunks
            ),
        },
        "rocketmq": {
            "chunk_count": domain_counts.get("rocketmq", 0),
            "chunk_ids": sorted(EXPECTED_ROCKETMQ_IDS.intersection(chunk_ids)),
            "active_kafka_chunk_count": domain_counts.get("kafka", 0),
            "pilot_unit_id": (
                rocketmq_unit.knowledge_unit_id if rocketmq_unit is not None else None
            ),
        },
        "datasets": {
            "pilot_version": pilot.version,
            "pilot_case_count": len(pilot.cases),
            "memory_p1_version": memory_p1.version,
            "memory_p1_case_count": len(memory_p1.cases),
        },
        "runtime_defaults": {
            "engine": settings.engine,
            "hybrid_rollout_percent": settings.hybrid_rollout_percent,
            "shadow_enabled": settings.shadow_enabled,
        },
        "legacy_compatibility": {
            "frozen": legacy_frozen,
            "chunk_count": legacy.get("chunk_count"),
            "manifest_sha256": legacy.get("corpus_manifest_sha256"),
            "kafka_chunk_count": len(
                {item for item in legacy_ids if str(item).startswith("kafka_")}
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline RocketMQ V4 knowledge repository preflight"
    )
    parser.add_argument("--knowledge-root", type=Path, default=KNOWLEDGE_V2_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--pilot-dataset", type=Path, default=PILOT_DATASET_PATH)
    parser.add_argument(
        "--memory-p1-dataset",
        type=Path,
        default=MEMORY_P1_DATASET_PATH,
    )
    parser.add_argument(
        "--legacy-manifest",
        type=Path,
        default=LEGACY_MANIFEST_PATH,
    )
    args = parser.parse_args(argv)
    try:
        result = build_rocketmq_v4_preflight(
            knowledge_root=args.knowledge_root,
            manifest_path=args.manifest,
            pilot_dataset_path=args.pilot_dataset,
            memory_p1_dataset_path=args.memory_p1_dataset,
            legacy_manifest_path=args.legacy_manifest,
        )
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        result = {
            "schema_version": "knowledge-rocketmq-v4-preflight-v1",
            "passed": False,
            "failure_reasons": ["PREFLIGHT_INPUT_INVALID"],
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
