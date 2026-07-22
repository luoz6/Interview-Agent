from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from app.services.config import get_embedding_settings
from app.services.knowledge_eval_dataset import (
    load_knowledge_retrieval_dataset,
)
from app.services.knowledge_eval_dataset_v2 import (
    load_knowledge_retrieval_dataset_v2,
)
from app.services.knowledge_ingestion import KnowledgeCorpusIngestor
from app.services.vector_store import PgVectorKnowledgeStore
from scripts.audit_stage44b1_artifacts import write_artifact_manifest
from scripts.build_knowledge_manifest_v2 import (
    KNOWLEDGE_V2_ROOT,
    build_manifest_v2,
)
from scripts.evaluate_knowledge_retrieval import evaluate_knowledge_retrieval
from scripts.evaluate_knowledge_retrieval_v2 import (
    evaluate_knowledge_retrieval_v2,
)
from scripts.load_knowledge_v2 import build_chunks_v2


CORPUS_VERSION = "stage44b1-zh-v2"
PILOT_VERSION = "stage44b1-knowledge-retrieval-v2-pilot"
V1_VERSION = "stage42-knowledge-retrieval-v1"
RC_TABLE_PREFIX = "knowledge_chunks_stage44b_rc"
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _stable_sha256(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_provider_metrics(provider) -> dict:
    snapshot = getattr(provider, "snapshot_metrics", None)
    if not callable(snapshot):
        return {
            "request_count": 0,
            "retry_count": 0,
            "error_counts": {},
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }
    raw = snapshot()
    return {
        key: raw[key]
        for key in (
            "request_count",
            "retry_count",
            "error_counts",
            "latency_p50_ms",
            "latency_p95_ms",
        )
        if key in raw
    }


def _validate_inputs(
    *, chunks, manifest: dict, pilot_dataset, v1_dataset, run_id: str
) -> None:
    if len(chunks) != 25:
        raise ValueError("Stage 44B1 acceptance requires exactly 25 chunks")
    if manifest.get("manifest_schema_version") != 2:
        raise ValueError("Stage 44B1 acceptance requires a v2 manifest")
    if manifest.get("corpus_version") != CORPUS_VERSION:
        raise ValueError("Stage 44B1 corpus version mismatch")
    if manifest.get("chunk_count") != 25:
        raise ValueError("Stage 44B1 manifest requires exactly 25 chunks")
    manifest_hash = manifest.get("corpus_manifest_sha256")
    if not isinstance(manifest_hash, str) or not manifest_hash:
        raise ValueError("Stage 44B1 manifest hash is required")
    if pilot_dataset.version != PILOT_VERSION:
        raise ValueError("Stage 44B1 pilot dataset version mismatch")
    if len(pilot_dataset.cases) != 12:
        raise ValueError("Stage 44B1 acceptance requires exactly 12 pilot cases")
    if v1_dataset.version != V1_VERSION:
        raise ValueError("Stage 44B1 frozen v1 dataset version mismatch")
    if len(v1_dataset.cases) != 30:
        raise ValueError("Stage 44B1 acceptance requires exactly 30 v1 cases")
    if not _SAFE_PATH_SEGMENT.fullmatch(run_id):
        raise ValueError("run_id must be one safe path segment")

    manifest_ids = {
        entry.get("chunk_id")
        for entry in manifest.get("chunks", [])
        if isinstance(entry, dict)
    }
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    if len(chunk_ids) != 25 or manifest_ids != chunk_ids:
        raise ValueError("Stage 44B1 manifest and chunk identities mismatch")
    for chunk in chunks:
        if chunk.metadata.get("corpus_version") != CORPUS_VERSION:
            raise ValueError("Stage 44B1 chunk corpus version mismatch")
        if chunk.metadata.get("corpus_manifest_sha256") != manifest_hash:
            raise ValueError("Stage 44B1 chunk manifest hash mismatch")


def _evaluation_cases_complete(evaluation: dict, expected_count: int) -> bool:
    cases = evaluation.get("cases", [])
    return len(cases) == expected_count and all(
        case.get("status") == "completed" for case in cases
    )


def _safe_case_payload(case: dict, *, replay_key: str) -> dict:
    return {
        "case_id": case["case_id"],
        "status": case["status"],
        "retrieved_ids": case["retrieved_ids"],
        "scores": case["scores"],
        "bound_evidence_ids": case["bound_evidence_ids"],
        "replayed_evidence_ids": case[replay_key],
        "latency_ms": case["latency_ms"],
    }


def _write_case_artifacts(
    destination: Path,
    *,
    group: str,
    cases: list[dict],
    replay_key: str,
) -> None:
    group_dir = destination / "retrieval-cases" / group
    group_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id", ""))
        if not _SAFE_PATH_SEGMENT.fullmatch(case_id) or case_id in seen:
            raise ValueError("evaluation case_id is not a safe unique file name")
        seen.add(case_id)
        payload = _safe_case_payload(case, replay_key=replay_key)
        (group_dir / f"{case_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )


def run_stage44b1_acceptance(
    *,
    repository,
    ingestor,
    chunks,
    manifest: dict,
    pilot_dataset,
    v1_dataset,
    run_id: str,
    run_dir: Path | str,
) -> dict:
    _validate_inputs(
        chunks=chunks,
        manifest=manifest,
        pilot_dataset=pilot_dataset,
        v1_dataset=v1_dataset,
        run_id=run_id,
    )

    ingestion = ingestor.ingest(chunks=chunks, manifest=manifest)
    pilot_evaluation = evaluate_knowledge_retrieval_v2(
        pilot_dataset, repository, vector_validity_rate=1.0
    )
    v1_evaluation = evaluate_knowledge_retrieval(v1_dataset, repository)
    provider = repository.embedding_provider
    manifest_hash = manifest["corpus_manifest_sha256"]

    evaluations_match_identity = all(
        evaluation.get("provider") == provider.provider_name
        and evaluation.get("model") == provider.model_name
        and evaluation.get("model_revision") == provider.model_revision
        and evaluation.get("corpus_version") == CORPUS_VERSION
        and evaluation.get("corpus_manifest_sha256") == manifest_hash
        for evaluation in (pilot_evaluation, v1_evaluation)
    )
    identity_matches = all(
        (
            ingestion.corpus_version == CORPUS_VERSION,
            ingestion.manifest_sha256 == manifest_hash,
            ingestion.provider_name == provider.provider_name,
            ingestion.model_name == provider.model_name,
            ingestion.model_revision == provider.model_revision,
            ingestion.dimension == provider.dimension,
            pilot_evaluation.get("dataset_version") == PILOT_VERSION,
            v1_evaluation.get("dataset_version") == V1_VERSION,
            evaluations_match_identity,
        )
    )
    ingestion_counts_match = all(
        (
            ingestion.discovered == 25,
            ingestion.activated == 25,
            ingestion.embedded + ingestion.reused == 25,
        )
    )
    pilot_complete = _evaluation_cases_complete(pilot_evaluation, 12)
    v1_complete = _evaluation_cases_complete(v1_evaluation, 30)
    pilot_passed = pilot_evaluation["metrics"].get("passed") is True
    v1_passed = v1_evaluation["metrics"].get("passed") is True

    failure_reasons: list[str] = []
    if not identity_matches:
        failure_reasons.append("identity_mismatch")
    if not ingestion_counts_match:
        failure_reasons.append("ingestion_count_mismatch")
    if not pilot_complete:
        failure_reasons.append("incomplete_or_degraded_pilot_cases")
    if not pilot_passed:
        failure_reasons.append("pilot_metrics_failed")
    if not v1_complete:
        failure_reasons.append("incomplete_or_degraded_v1_cases")
    if not v1_passed:
        failure_reasons.append("v1_metrics_failed")

    metrics_payload = {
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "run_id": run_id,
        "provider_name": provider.provider_name,
        "model_name": provider.model_name,
        "model_revision": provider.model_revision,
        "dimension": provider.dimension,
        "corpus_version": ingestion.corpus_version,
        "corpus_manifest_sha256": ingestion.manifest_sha256,
        "chunk_count": ingestion.activated,
        "ingestion": {
            "discovered": ingestion.discovered,
            "embedded": ingestion.embedded,
            "reused": ingestion.reused,
            "activated": ingestion.activated,
        },
        "storage_strategy": "exact_pgvector_cosine",
        "pilot_dataset_version": pilot_dataset.version,
        "pilot_dataset_sha256": _stable_sha256(pilot_dataset),
        "pilot_metrics": pilot_evaluation["metrics"],
        "v1_dataset_version": v1_dataset.version,
        "v1_dataset_sha256": _stable_sha256(v1_dataset),
        "v1_metrics": v1_evaluation["metrics"],
        "provider_metrics": _safe_provider_metrics(provider),
    }

    destination = Path(run_dir)
    _write_case_artifacts(
        destination,
        group="pilot-v2",
        cases=pilot_evaluation["cases"],
        replay_key="replayed_evidence_ids",
    )
    _write_case_artifacts(
        destination,
        group="frozen-v1",
        cases=v1_evaluation["cases"],
        replay_key="reused_evidence_ids",
    )
    (destination / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    report_lines = [
        "# Stage 44B1 Chinese Corpus Acceptance",
        "",
        f"Status: {'PASS' if metrics_payload['passed'] else 'FAIL'}",
        f"Run ID: {run_id}",
        f"Provider: {provider.provider_name}",
        f"Model: {provider.model_name}",
        f"Model revision: {provider.model_revision}",
        f"Corpus version: {ingestion.corpus_version}",
        f"Corpus chunks: {ingestion.activated}",
        f"Embedded chunks: {ingestion.embedded}",
        f"Reused chunks: {ingestion.reused}",
        "Storage strategy: exact_pgvector_cosine",
        f"Pilot dataset version: {pilot_dataset.version}",
        f"Frozen v1 dataset version: {v1_dataset.version}",
        f"Pilot retrieval p95 ms: {pilot_evaluation['metrics']['p95_latency_ms']}",
        f"Frozen v1 retrieval p95 ms: {v1_evaluation['metrics']['p95_latency_ms']}",
        f"Failure reasons: {', '.join(failure_reasons) if failure_reasons else 'none'}",
    ]
    (destination / "report.md").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8"
    )
    write_artifact_manifest(destination, run_id=run_id)
    return metrics_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Stage 44B1 acceptance")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if os.getenv("RUN_SILICONFLOW_ACCEPTANCE") != "1":
        raise RuntimeError("RUN_SILICONFLOW_ACCEPTANCE=1 is required")
    if os.getenv("EMBEDDING_PROVIDER") != "siliconflow":
        raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")
    revision = os.getenv("EMBEDDING_MODEL_REVISION", "")
    if not revision or revision == "siliconflow-current":
        raise RuntimeError("a release-specific EMBEDDING_MODEL_REVISION is required")
    if os.getenv("PGVECTOR_TABLE") != RC_TABLE_PREFIX:
        raise RuntimeError(f"PGVECTOR_TABLE={RC_TABLE_PREFIX} is required")

    settings = get_embedding_settings()
    if settings.provider_name != "siliconflow":
        raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")
    if settings.model_revision != revision:
        raise RuntimeError("embedding model revision mismatch")

    manifest = build_manifest_v2(
        KNOWLEDGE_V2_ROOT, corpus_version=CORPUS_VERSION
    )
    chunks = build_chunks_v2(KNOWLEDGE_V2_ROOT, manifest=manifest)
    pilot_dataset = load_knowledge_retrieval_dataset_v2(
        expected_case_count=12, manifest=manifest
    )
    v1_dataset = load_knowledge_retrieval_dataset()
    repository = PgVectorKnowledgeStore.from_env()
    ingestor = KnowledgeCorpusIngestor(
        store=repository, provider=repository.embedding_provider
    )
    metrics = run_stage44b1_acceptance(
        repository=repository,
        ingestor=ingestor,
        chunks=chunks,
        manifest=manifest,
        pilot_dataset=pilot_dataset,
        v1_dataset=v1_dataset,
        run_id=args.run_id,
        run_dir=args.run_dir,
    )
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
