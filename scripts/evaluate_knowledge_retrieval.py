from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_eval_dataset import (
    KnowledgeRetrievalDataset,
    load_knowledge_retrieval_dataset,
)
from app.services.knowledge_eval_metrics import (
    KnowledgeRetrievalObservation,
    calculate_knowledge_retrieval_metrics,
)
from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore


DEFAULT_OUTPUT_PATH = Path("tmp/stage42-knowledge-retrieval.json")


def evaluate_knowledge_retrieval(
    dataset: KnowledgeRetrievalDataset,
    repository,
) -> dict:
    warmup_ms = _warm_repository(repository)
    observations: list[KnowledgeRetrievalObservation] = []
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
        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        retrieved_ids = [chunk.chunk_id for chunk in chunks]
        bound_ids = retrieved_ids[:1] if case.category != "negative" else []
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

        reused_ids: list[str] = []
        if bound_ids and len(expected_hashes) == len(bound_ids):
            try:
                lookup = repository.get_by_ids(
                    bound_ids,
                    expected_hashes=expected_hashes,
                )
                reused_ids = [chunk.chunk_id for chunk in lookup.found]
                if lookup.missing or lookup.version_mismatch:
                    status = "degraded"
            except Exception:
                status = "degraded"

        observation = KnowledgeRetrievalObservation(
            case_id=case.case_id,
            retrieved_ids=retrieved_ids,
            bound_evidence_ids=bound_ids,
            reused_evidence_ids=reused_ids,
            latency_ms=latency_ms,
        )
        observations.append(observation)
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "status": status,
                "retrieved_ids": retrieved_ids,
                "scores": {
                    chunk.chunk_id: round(float(chunk.score or 0.0), 6)
                    for chunk in chunks
                },
                "bound_evidence_ids": bound_ids,
                "reused_evidence_ids": reused_ids,
                "latency_ms": latency_ms,
            }
        )

    metrics = calculate_knowledge_retrieval_metrics(dataset, observations)
    corpus_hash = next(iter(manifest_hashes)) if len(manifest_hashes) == 1 else ""
    return {
        "dataset_version": dataset.version,
        "corpus_manifest_sha256": corpus_hash,
        "warmup_ms": warmup_ms,
        "metrics": metrics.model_dump(mode="json"),
        "cases": case_results,
    }


def write_evaluation_result(result: dict, output_path: Path | str) -> None:
    safe_result = {
        key: result[key]
        for key in (
            "dataset_version",
            "corpus_manifest_sha256",
            "warmup_ms",
            "metrics",
            "cases",
        )
        if key in result
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(safe_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _warm_repository(repository) -> float:
    embed_text = getattr(repository, "embed_text", None)
    if not callable(embed_text):
        return 0.0
    started_at = perf_counter()
    embed_text("knowledge retrieval warmup")
    return round((perf_counter() - started_at) * 1000, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Stage 42 knowledge retrieval")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    dataset = load_knowledge_retrieval_dataset(args.dataset) if args.dataset else load_knowledge_retrieval_dataset()
    repository = PgVectorKnowledgeStore.from_env()
    result = evaluate_knowledge_retrieval(dataset, repository)
    write_evaluation_result(result, args.output)
    metrics = result["metrics"]
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    print(f"artifact={args.output}")
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
