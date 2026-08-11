from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.runtime.config.compatibility import get_embedding_settings
from app.services.knowledge_eval_dataset import load_knowledge_retrieval_dataset
from app.services.knowledge_eval_dataset_v2 import (
    load_knowledge_retrieval_dataset_v2,
)
from app.services.knowledge_ingestion import KnowledgeReleaseService
from app.adapters.pgvector.repository import PgVectorKnowledgeStore
from scripts.build_knowledge_manifest import build_manifest
from scripts.build_knowledge_manifest_v2 import KNOWLEDGE_V2_ROOT, build_manifest_v2
from scripts.load_knowledge import KNOWLEDGE_ROOT, build_chunks
from scripts.load_knowledge_v2 import build_chunks_v2
from scripts.knowledge_acceptance_stage44a import (
    CORPUS_VERSION as STAGE44A_CORPUS_VERSION,
    run_stage44a_acceptance,
)
from scripts.knowledge_acceptance_stage44b1 import (
    CORPUS_VERSION as STAGE44B1_CORPUS_VERSION,
    run_stage44b1_acceptance,
)


STAGE44B1_TABLE = "knowledge_chunks_stage44b_rc"


def _require_common_safety() -> None:
    if os.getenv("RUN_SILICONFLOW_ACCEPTANCE") != "1":
        raise RuntimeError("RUN_SILICONFLOW_ACCEPTANCE=1 is required")
    if os.getenv("EMBEDDING_PROVIDER") != "siliconflow":
        raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")


def _run_stage44a(*, run_id: str, run_dir: Path) -> dict:
    _require_common_safety()
    settings = get_embedding_settings()
    if settings.provider_name != "siliconflow":
        raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")
    if settings.model_revision == "siliconflow-current":
        raise RuntimeError("a release-specific EMBEDDING_MODEL_REVISION is required")

    manifest = build_manifest(
        KNOWLEDGE_ROOT,
        corpus_version=STAGE44A_CORPUS_VERSION,
    )
    chunks = build_chunks(KNOWLEDGE_ROOT, manifest=manifest)
    dataset = load_knowledge_retrieval_dataset()
    repository = PgVectorKnowledgeStore.from_env()
    ingestor = KnowledgeReleaseService(
        store=repository,
        provider=repository.embedding_provider,
    )
    return run_stage44a_acceptance(
        repository=repository,
        ingestor=ingestor,
        dataset=dataset,
        chunks=chunks,
        run_id=run_id,
        run_dir=run_dir,
    )


def _run_stage44b1(*, run_id: str, run_dir: Path) -> dict:
    _require_common_safety()
    revision = os.getenv("EMBEDDING_MODEL_REVISION", "")
    if not revision or revision == "siliconflow-current":
        raise RuntimeError("a release-specific EMBEDDING_MODEL_REVISION is required")
    if os.getenv("PGVECTOR_TABLE") != STAGE44B1_TABLE:
        raise RuntimeError(f"PGVECTOR_TABLE={STAGE44B1_TABLE} is required")

    settings = get_embedding_settings()
    if settings.provider_name != "siliconflow":
        raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")
    if settings.model_revision != revision:
        raise RuntimeError("embedding model revision mismatch")

    manifest = build_manifest_v2(
        KNOWLEDGE_V2_ROOT,
        corpus_version=STAGE44B1_CORPUS_VERSION,
    )
    chunks = build_chunks_v2(KNOWLEDGE_V2_ROOT, manifest=manifest)
    pilot_dataset = load_knowledge_retrieval_dataset_v2(
        expected_case_count=12,
        manifest=manifest,
    )
    v1_dataset = load_knowledge_retrieval_dataset()
    repository = PgVectorKnowledgeStore.from_env()
    ingestor = KnowledgeReleaseService(
        store=repository,
        provider=repository.embedding_provider,
    )
    return run_stage44b1_acceptance(
        repository=repository,
        ingestor=ingestor,
        chunks=chunks,
        manifest=manifest,
        pilot_dataset=pilot_dataset,
        v1_dataset=v1_dataset,
        run_id=run_id,
        run_dir=run_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a versioned knowledge acceptance profile"
    )
    parser.add_argument("profile", choices=("stage44a", "stage44b1"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    runner = _run_stage44a if args.profile == "stage44a" else _run_stage44b1
    metrics = runner(run_id=args.run_id, run_dir=args.run_dir)
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
