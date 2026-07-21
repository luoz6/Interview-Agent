from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from app.services.config import get_embedding_settings
from app.services.knowledge_eval_dataset import load_knowledge_retrieval_dataset
from app.services.knowledge_ingestion import KnowledgeCorpusIngestor
from app.services.vector_store import PgVectorKnowledgeStore
from scripts.audit_stage44a_artifacts import write_artifact_manifest
from scripts.build_knowledge_manifest import build_manifest
from scripts.evaluate_knowledge_retrieval import evaluate_knowledge_retrieval
from scripts.load_knowledge import KNOWLEDGE_ROOT, build_chunks


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
        "provider_metrics": _safe_provider_metrics(provider),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Stage 44A acceptance")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if os.getenv("RUN_SILICONFLOW_ACCEPTANCE") != "1":
        raise RuntimeError("RUN_SILICONFLOW_ACCEPTANCE=1 is required")
    settings = get_embedding_settings()
    if settings.provider_name != "siliconflow":
        raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")
    if settings.model_revision == "siliconflow-current":
        raise RuntimeError("a release-specific EMBEDDING_MODEL_REVISION is required")

    manifest = build_manifest(KNOWLEDGE_ROOT, corpus_version=CORPUS_VERSION)
    chunks = build_chunks(KNOWLEDGE_ROOT, manifest=manifest)
    dataset = load_knowledge_retrieval_dataset()
    repository = PgVectorKnowledgeStore.from_env()
    ingestor = KnowledgeCorpusIngestor(
        store=repository,
        provider=repository.embedding_provider,
    )
    metrics = run_stage44a_acceptance(
        repository=repository,
        ingestor=ingestor,
        dataset=dataset,
        chunks=chunks,
        run_id=args.run_id,
        run_dir=args.run_dir,
    )
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
