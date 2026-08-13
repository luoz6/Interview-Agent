from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.pgvector.repository import PgVectorKnowledgeStore
from app.services.knowledge_eval_artifacts_v3 import (
    build_engine_identity_v3,
    evaluate_knowledge_engine_v3,
    write_frozen_eval_artifact,
)
from app.services.knowledge_eval_dataset_v3 import load_knowledge_retrieval_dataset_v3
from scripts.build_knowledge_eval_v3_machine_preannotations import (
    DEFAULT_MANIFEST,
    DEFAULT_OUTPUT_DIR,
    validate_machine_dataset,
)
from scripts.evaluate_knowledge_retrieval_v3 import (
    _code_tree_sha256,
    _engine,
    _engine_version,
    _git_revision,
    _profile,
)


def run_legacy_machine_diagnostic(
    *,
    dataset_path: Path,
    provenance_path: Path,
    manifest_path: Path,
    split: str,
    output_path: Path,
) -> dict:
    candidate_summary = validate_machine_dataset(
        dataset_path,
        provenance_path,
        manifest_path,
    )
    if candidate_summary["eligible_as_independent_eval_evidence"]:
        raise ValueError("machine diagnostic requires a non-independent candidate")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = load_knowledge_retrieval_dataset_v3(
        dataset_path,
        manifest=manifest,
        require_release_shape=False,
    )
    if dataset.governance is not None:
        raise ValueError("machine diagnostic cannot claim human governance")

    repository = PgVectorKnowledgeStore.from_env(schema_mode="validate")
    profile = _profile("legacy")
    engine = _engine("legacy", repository)
    try:
        identity = build_engine_identity_v3(
            engine_version=_engine_version("legacy", "weighted-rrf"),
            code_revision=_git_revision(),
            code_tree_sha256=_code_tree_sha256(),
            profile=profile,
            repository=repository,
            corpus_version=str(manifest["corpus_version"]),
            corpus_manifest_sha256=str(manifest["corpus_manifest_sha256"]),
        )
        artifact = evaluate_knowledge_engine_v3(
            dataset,
            engine,
            repository,
            split=split,
            profile=profile,
            identity=identity,
        )
        write_frozen_eval_artifact(artifact, output_path)
        provider_metrics = repository.embedding_provider.snapshot_metrics()
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()

    metrics = artifact.metrics
    return {
        "status": "machine_preannotation_legacy_diagnostic_complete",
        "independent_eval_evidence": False,
        "artifact": str(output_path),
        "artifact_sha256": artifact.artifact_sha256,
        "dataset_sha256": artifact.dataset_sha256,
        "split": split,
        "case_count": metrics.case_count,
        "recall_at_5": metrics.recall_at_5,
        "mrr_at_5": metrics.mrr_at_5,
        "ndcg_at_5": metrics.ndcg_at_5,
        "hit_at_1": metrics.hit_at_1,
        "hard_negative_false_positive_rate": metrics.hard_negative_false_positive_rate,
        "no_evidence_f1": metrics.no_evidence_f1,
        "filter_correctness_rate": metrics.filter_correctness_rate,
        "excluded_chunk_violation_rate": metrics.excluded_chunk_violation_rate,
        "evidence_replay_stability_rate": metrics.evidence_replay_stability_rate,
        "observation_completeness_rate": metrics.observation_completeness_rate,
        "p95_latency_ms": metrics.p95_latency_ms,
        "provider_request_count": provider_metrics["request_count"],
        "provider_retry_count": provider_metrics["retry_count"],
        "provider_error_counts": provider_metrics["error_counts"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a Legacy diagnostic for a non-independent Eval V3 machine candidate"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT_DIR / "dataset.json")
    parser.add_argument(
        "--provenance", type=Path, default=DEFAULT_OUTPUT_DIR / "provenance.json"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--split", choices=("tuning", "holdout"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = run_legacy_machine_diagnostic(
        dataset_path=args.dataset,
        provenance_path=args.provenance,
        manifest_path=args.manifest,
        split=args.split,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
