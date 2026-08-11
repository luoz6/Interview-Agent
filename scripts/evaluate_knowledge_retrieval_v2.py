from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_eval_dataset_v2 import (
    KnowledgeRetrievalDatasetV2,
    load_knowledge_retrieval_dataset_v2,
)
from app.services.knowledge_eval_metrics_v2 import (
    KnowledgeRetrievalObservationV2,
    RetrievedKnowledgeItemV2,
    calculate_knowledge_retrieval_metrics_v2,
)
from app.adapters.pgvector.repository import KnowledgeChunk, PgVectorKnowledgeStore


DEFAULT_OUTPUT_PATH = Path("tmp/stage44b1-knowledge-retrieval-v2.json")
DEFAULT_MANIFEST_PATH = Path("app/data/knowledge_v2/manifest.json")


def evaluate_knowledge_retrieval_v2(
    dataset: KnowledgeRetrievalDatasetV2,
    repository,
    *,
    vector_validity_rate: float,
) -> dict:
    warmup_ms = _warm_repository(repository)
    observations: list[KnowledgeRetrievalObservationV2] = []
    case_results: list[dict] = []
    manifest_hashes: set[str] = set()

    for case in dataset.cases:
        started_at = perf_counter()
        status = "completed"
        try:
            raw_chunks = repository.search(
                case.query_text,
                job_tags=case.canonical_tags,
                source_types=case.source_types,
                limit=case.top_k,
            )
            chunks = [
                chunk
                if isinstance(chunk, KnowledgeChunk)
                else KnowledgeChunk.model_validate(chunk)
                for chunk in raw_chunks
            ]
        except Exception:
            chunks = []
            status = "degraded"

        retrieved_items = [
            RetrievedKnowledgeItemV2(
                chunk_id=chunk.chunk_id,
                domain=chunk.domain,
                source_type=chunk.source_type,
                tags=chunk.tags,
            )
            for chunk in chunks
        ]
        retrieved_ids = [item.chunk_id for item in retrieved_items]
        bound_ids = retrieved_ids[:1]
        expected_hashes: dict[str, str] = {}
        for chunk in chunks:
            manifest_hash = chunk.metadata.get("corpus_manifest_sha256")
            if isinstance(manifest_hash, str) and manifest_hash:
                manifest_hashes.add(manifest_hash)
            if chunk.chunk_id not in bound_ids:
                continue
            content_hash = chunk.metadata.get("content_sha256")
            if isinstance(content_hash, str) and content_hash:
                expected_hashes[chunk.chunk_id] = content_hash

        replayed_ids: list[str] = []
        if bound_ids and len(expected_hashes) == len(bound_ids):
            try:
                lookup = repository.get_by_ids(
                    bound_ids, expected_hashes=expected_hashes
                )
                replayed_ids = [
                    chunk.chunk_id
                    if isinstance(chunk, KnowledgeChunk)
                    else KnowledgeChunk.model_validate(chunk).chunk_id
                    for chunk in lookup.found
                ]
                if lookup.missing or lookup.version_mismatch:
                    status = "degraded"
            except Exception:
                status = "degraded"
        elif bound_ids:
            status = "degraded"

        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        observations.append(
            KnowledgeRetrievalObservationV2(
                case_id=case.case_id,
                retrieved=retrieved_items,
                bound_evidence_ids=bound_ids,
                replayed_evidence_ids=replayed_ids,
                latency_ms=latency_ms,
            )
        )
        case_results.append(
            {
                "case_id": case.case_id,
                "status": status,
                "retrieved_ids": retrieved_ids,
                "scores": {
                    chunk.chunk_id: round(float(chunk.score or 0.0), 6)
                    for chunk in chunks
                },
                "bound_evidence_ids": bound_ids,
                "replayed_evidence_ids": replayed_ids,
                "latency_ms": latency_ms,
            }
        )

    metrics = calculate_knowledge_retrieval_metrics_v2(
        dataset, observations, vector_validity_rate=vector_validity_rate
    )
    provider = getattr(repository, "embedding_provider", None)
    corpus_hash = next(iter(manifest_hashes)) if len(manifest_hashes) == 1 else ""
    return {
        "dataset_version": dataset.version,
        "corpus_manifest_sha256": corpus_hash,
        "provider": getattr(provider, "provider_name", ""),
        "model": getattr(provider, "model_name", ""),
        "model_revision": getattr(provider, "model_revision", ""),
        "corpus_version": _active_corpus_version(repository),
        "warmup_ms": warmup_ms,
        "metrics": metrics.model_dump(mode="json"),
        "cases": case_results,
    }


def write_evaluation_result_v2(result: dict, output_path: Path | str) -> None:
    allowed_keys = (
        "dataset_version",
        "corpus_manifest_sha256",
        "provider",
        "model",
        "model_revision",
        "corpus_version",
        "warmup_ms",
        "metrics",
        "cases",
    )
    safe_result = {key: result[key] for key in allowed_keys if key in result}
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(safe_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _warm_repository(repository) -> float:
    warm_embedding = getattr(repository, "warm_embedding", None)
    if not callable(warm_embedding):
        return 0.0
    started_at = perf_counter()
    warm_embedding("知识检索预热")
    return round((perf_counter() - started_at) * 1000, 3)


def _active_corpus_version(repository) -> str:
    getter = getattr(repository, "get_active_corpus_version", None)
    if not callable(getter):
        return ""
    try:
        return getter() or ""
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 Stage 44B 中文知识检索")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--expected-case-count", type=int, default=12)
    parser.add_argument("--vector-validity-rate", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    dataset_path = args.dataset or Path(
        "tests/golden/knowledge_retrieval_v2_pilot.json"
    )
    dataset = load_knowledge_retrieval_dataset_v2(
        dataset_path,
        expected_case_count=args.expected_case_count,
        manifest=manifest,
    )
    repository = PgVectorKnowledgeStore.from_env()
    result = evaluate_knowledge_retrieval_v2(
        dataset, repository, vector_validity_rate=args.vector_validity_rate
    )
    write_evaluation_result_v2(result, args.output)
    print(json.dumps(result["metrics"], ensure_ascii=False, sort_keys=True))
    print(f"artifact={args.output}")
    return 0 if result["metrics"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

