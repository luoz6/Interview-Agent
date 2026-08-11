from __future__ import annotations

import json
from pathlib import Path
import re

from scripts.release_artifact_audit import (
    write_stage44a_manifest as write_artifact_manifest,
)
from scripts.evaluate_knowledge_retrieval import evaluate_knowledge_retrieval
from scripts.knowledge_acceptance_support import safe_provider_metrics


CORPUS_VERSION = "stage44a-bge-m3-v1"
DATASET_VERSION = "stage42-knowledge-retrieval-v1"
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _manifest_from_chunks(chunks) -> dict:
    manifest_hashes = {
        chunk.metadata.get("corpus_manifest_sha256") for chunk in chunks
    }
    corpus_versions = {chunk.metadata.get("corpus_version") for chunk in chunks}
    if len(manifest_hashes) != 1 or None in manifest_hashes:
        raise ValueError("chunks must share one corpus manifest hash")
    if corpus_versions != {CORPUS_VERSION}:
        raise ValueError("chunks must use the Stage 44A corpus version")
    entries = []
    for chunk in chunks:
        content_sha256 = chunk.metadata.get("content_sha256")
        if not isinstance(content_sha256, str) or not content_sha256:
            raise ValueError("chunk content hash is required")
        entries.append(
            {
                "chunk_id": chunk.chunk_id,
                "content_sha256": content_sha256,
            }
        )
    return {
        "corpus_version": CORPUS_VERSION,
        "corpus_manifest_sha256": next(iter(manifest_hashes)),
        "chunk_count": len(chunks),
        "chunks": sorted(entries, key=lambda item: item["chunk_id"]),
    }


def run_stage44a_acceptance(
    *,
    repository,
    ingestor,
    dataset,
    chunks,
    run_id: str,
    run_dir: Path | str,
) -> dict:
    if len(chunks) != 25:
        raise ValueError("Stage 44A acceptance requires exactly 25 chunks")
    if dataset.version != DATASET_VERSION:
        raise ValueError("Stage 44A acceptance dataset version mismatch")
    if not run_id or Path(run_id).name != run_id:
        raise ValueError("run_id must be one safe path segment")

    manifest = _manifest_from_chunks(chunks)
    ingestion = ingestor.ingest(chunks=chunks, manifest=manifest)
    evaluation = evaluate_knowledge_retrieval(dataset, repository)
    provider = repository.embedding_provider

    identity_matches = all(
        (
            ingestion.corpus_version == CORPUS_VERSION,
            ingestion.manifest_sha256 == manifest["corpus_manifest_sha256"],
            ingestion.discovered == 25,
            ingestion.activated == 25,
            ingestion.provider_name == provider.provider_name,
            ingestion.model_name == provider.model_name,
            ingestion.model_revision == provider.model_revision,
            ingestion.dimension == provider.dimension,
            evaluation.get("provider") == provider.provider_name,
            evaluation.get("model") == provider.model_name,
            evaluation.get("model_revision") == provider.model_revision,
            evaluation.get("corpus_version") == CORPUS_VERSION,
            evaluation.get("corpus_manifest_sha256")
            == manifest["corpus_manifest_sha256"],
        )
    )
    cases_complete = len(evaluation["cases"]) == len(dataset.cases) and all(
        case.get("status") == "completed" for case in evaluation["cases"]
    )
    retrieval_passed = evaluation["metrics"].get("passed") is True
    failure_reasons = []
    if not identity_matches:
        failure_reasons.append("identity_mismatch")
    if not cases_complete:
        failure_reasons.append("incomplete_or_degraded_cases")
    if not retrieval_passed:
        failure_reasons.append("retrieval_metrics_failed")

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
        "storage_strategy": "exact_pgvector_cosine",
        "dataset_version": dataset.version,
        "retrieval_metrics": evaluation["metrics"],
        "provider_metrics": safe_provider_metrics(provider),
    }

    destination = Path(run_dir)
    cases_dir = destination / "retrieval-cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    seen_case_ids: set[str] = set()
    for case in evaluation["cases"]:
        case_id = str(case["case_id"])
        if not _SAFE_CASE_ID.fullmatch(case_id) or case_id in seen_case_ids:
            raise ValueError("evaluation case_id is not a safe unique file name")
        seen_case_ids.add(case_id)
        safe_case = {
            key: case[key]
            for key in (
                "case_id",
                "category",
                "status",
                "retrieved_ids",
                "scores",
                "bound_evidence_ids",
                "reused_evidence_ids",
                "latency_ms",
            )
        }
        (cases_dir / f"{case_id}.json").write_text(
            json.dumps(safe_case, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    (destination / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    report_lines = [
        "# Stage 44A Remote BGE-M3 Acceptance",
        "",
        f"Status: {'PASS' if metrics_payload['passed'] else 'FAIL'}",
        f"Run ID: {run_id}",
        f"Provider: {provider.provider_name}",
        f"Model: {provider.model_name}",
        f"Model revision: {provider.model_revision}",
        f"Corpus version: {ingestion.corpus_version}",
        f"Corpus chunks: {ingestion.activated}",
        "Storage strategy: exact_pgvector_cosine",
        f"Dataset version: {dataset.version}",
        f"Retrieval p95 ms: {evaluation['metrics']['p95_latency_ms']}",
        f"Failure reasons: {', '.join(failure_reasons) if failure_reasons else 'none'}",
    ]
    (destination / "report.md").write_text(
        "\n".join(report_lines) + "\n",
        encoding="utf-8",
    )
    write_artifact_manifest(destination, run_id=run_id)
    return metrics_payload
