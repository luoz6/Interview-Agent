from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.knowledge import ExactTermLexicalRetriever
from app.adapters.pgvector.repository import PgVectorKnowledgeStore
from app.application.knowledge import (
    HybridKnowledgeRetrievalService,
    KnowledgeRetrievalService,
)
from app.application.knowledge.retrieval_profiles import compatibility_profile
from app.domain.knowledge.retrieval import ResolvedRetrievalProfile
from app.services.knowledge_eval_artifacts_v3 import (
    build_engine_identity_v3,
    canonical_sha256,
    compare_knowledge_eval_artifacts_v3,
    evaluate_knowledge_engine_v3,
    load_eval_artifact_v3,
    write_frozen_eval_artifact,
    write_retrieval_diagnostic_snapshots_v1,
)
from app.services.knowledge_eval_dataset_v3 import (
    DEFAULT_DATASET_V3_PATH,
    load_knowledge_retrieval_dataset_v3,
)


DEFAULT_MANIFEST_PATH = Path("app/data/knowledge_v2/manifest.json")
ABLATION_CHOICES = (
    "semantic-only",
    "lexical-only",
    "unweighted-rrf",
    "weighted-rrf",
    "rank-normalized-score",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate, run, and compare privacy-safe Knowledge Eval V3 "
            "diagnostic artifacts"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_V3_PATH)
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)

    run = subparsers.add_parser("run")
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_V3_PATH)
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    run.add_argument("--engine", choices=("legacy", "hybrid-v2"), required=True)
    run.add_argument("--ablation", choices=ABLATION_CHOICES)
    run.add_argument("--profile", type=Path)
    run.add_argument("--split", choices=("tuning", "holdout"), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--code-revision")
    run.add_argument("--vector-validity-rate", type=float, default=1.0)
    run.add_argument(
        "--diagnostic-snapshot-root",
        type=Path,
        help="Optional frozen sidecar root; writes <artifact-sha>/<case-id>.json.",
    )

    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "validate":
        dataset, _ = _load_dataset(args.dataset, args.manifest)
        print(
            json.dumps(
                {
                    "dataset_version": dataset.version,
                    "case_count": len(dataset.cases),
                    "tuning_count": sum(case.split == "tuning" for case in dataset.cases),
                    "holdout_count": sum(case.split == "holdout" for case in dataset.cases),
                    "status": "valid",
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "compare":
        baseline = load_eval_artifact_v3(args.baseline)
        paired = compare_knowledge_eval_artifacts_v3(
            baseline,
            load_eval_artifact_v3(args.candidate),
        )
        write_frozen_eval_artifact(paired, args.output)
        print(
            json.dumps(
                {
                    "artifact": str(args.output),
                    "artifact_sha256": paired.artifact_sha256,
                    "split": paired.split,
                    "comparison_status": "diagnostic",
                },
                sort_keys=True,
            )
        )
        return 0

    dataset, manifest = _load_dataset(args.dataset, args.manifest)
    if args.engine == "legacy" and (args.ablation is not None or args.profile is not None):
        raise ValueError("Legacy evaluation does not accept Hybrid ablation/profile")
    ablation = args.ablation or "weighted-rrf"
    repository = PgVectorKnowledgeStore.from_env(schema_mode="validate")
    profile = _profile(args.engine, ablation=ablation, profile_path=args.profile)
    engine = _engine(args.engine, repository, ablation=ablation)
    try:
        identity = build_engine_identity_v3(
            engine_version=_engine_version(args.engine, ablation),
            code_revision=args.code_revision or _git_revision(),
            code_tree_sha256=_code_tree_sha256(),
            profile=profile,
            repository=repository,
            corpus_version=str(manifest["corpus_version"]),
            corpus_manifest_sha256=str(manifest["corpus_manifest_sha256"]),
        )
        diagnostic_results = {}
        artifact = evaluate_knowledge_engine_v3(
            dataset,
            engine,
            repository,
            split=args.split,
            profile=profile,
            identity=identity,
            vector_validity_rate=args.vector_validity_rate,
            result_observer=(
                lambda case_id, result: diagnostic_results.__setitem__(case_id, result)
                if args.diagnostic_snapshot_root is not None
                else None
            ),
        )
        write_frozen_eval_artifact(artifact, args.output)
        if args.diagnostic_snapshot_root is not None:
            try:
                write_retrieval_diagnostic_snapshots_v1(
                    artifact,
                    diagnostic_results,
                    args.diagnostic_snapshot_root,
                )
            except Exception:
                args.output.unlink(missing_ok=True)
                raise
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()
    print(
        json.dumps(
            {
                "artifact": str(args.output),
                "artifact_sha256": artifact.artifact_sha256,
                "case_count": artifact.metrics.case_count,
                "engine_version": artifact.identity.engine_version,
                "profile_id": artifact.identity.profile_id,
                "split": artifact.split,
            },
            sort_keys=True,
        )
    )
    return 0


def _load_dataset(dataset_path: Path, manifest_path: Path):
    manifest = _load_manifest(manifest_path)
    dataset = load_knowledge_retrieval_dataset_v3(
        dataset_path,
        manifest=manifest,
        require_diagnostic_integrity=True,
    )
    return dataset, manifest


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _profile(
    engine: str,
    *,
    ablation: str = "weighted-rrf",
    profile_path: Path | None = None,
) -> ResolvedRetrievalProfile:
    legacy = compatibility_profile(minimum_score=0.45, evidence_limit=5)
    if engine == "legacy":
        return legacy.model_copy(update={"profile_id": "eval-legacy-v3"})
    if profile_path is not None:
        profile = ResolvedRetrievalProfile.model_validate_json(
            profile_path.read_text(encoding="utf-8")
        )
        _validate_ablation_profile(profile, ablation)
        return profile
    updates = {
        "semantic-only": {
            "semantic_enabled": True,
            "lexical_enabled": False,
            "semantic_weight": 1.0,
            "lexical_weight": 1.0,
        },
        "lexical-only": {
            "semantic_enabled": False,
            "lexical_enabled": True,
            "semantic_weight": 1.0,
            "lexical_weight": 1.0,
        },
        "unweighted-rrf": {
            "semantic_enabled": True,
            "lexical_enabled": True,
            "semantic_weight": 1.0,
            "lexical_weight": 1.0,
        },
        "weighted-rrf": {
            "semantic_enabled": True,
            "lexical_enabled": True,
            "semantic_weight": 1.0,
            "lexical_weight": 1.2,
        },
        "rank-normalized-score": {
            "semantic_enabled": True,
            "lexical_enabled": True,
            "semantic_weight": 1.0,
            "lexical_weight": 1.0,
            "fusion_strategy": "rank_normalized_score",
        },
    }[ablation]
    payload = {
        "profile_id": f"eval-{ablation}-v3",
        "profile_version": "hybrid-v1",
        "semantic_enabled": True,
        "lexical_enabled": True,
        "semantic_candidate_limit": 20,
        "lexical_candidate_limit": 20,
        "fusion_candidate_limit": 15,
        "rerank_candidate_limit": 12,
        "evidence_limit": 5,
        "minimum_score": 0.45,
        "rrf_k": 60,
        "semantic_weight": 1.0,
        "lexical_weight": 1.0,
        "semantic_timeout_ms": 1500,
        "lexical_timeout_ms": 500,
        "rerank_timeout_ms": 1500,
        "total_timeout_ms": 3000,
    }
    payload.update(updates)
    return ResolvedRetrievalProfile(**payload)


def _validate_ablation_profile(profile, ablation: str) -> None:
    if ablation == "semantic-only" and not (
        profile.semantic_enabled and not profile.lexical_enabled
    ):
        raise ValueError("semantic-only ablation requires only semantic channel")
    if ablation == "lexical-only" and not (
        profile.lexical_enabled and not profile.semantic_enabled
    ):
        raise ValueError("lexical-only ablation requires only lexical channel")
    if ablation not in {"semantic-only", "lexical-only"} and not (
        profile.semantic_enabled and profile.lexical_enabled
    ):
        raise ValueError("fusion ablation requires semantic and lexical channels")
    if ablation == "unweighted-rrf" and (
        profile.semantic_weight != profile.lexical_weight
        or profile.fusion_strategy != "weighted_rrf"
    ):
        raise ValueError("unweighted RRF requires equal weights and RRF strategy")
    if ablation == "weighted-rrf" and (
        profile.semantic_weight == profile.lexical_weight
        or profile.fusion_strategy != "weighted_rrf"
    ):
        raise ValueError("weighted RRF requires unequal weights and RRF strategy")
    if ablation == "rank-normalized-score" and (
        profile.fusion_strategy != "rank_normalized_score"
    ):
        raise ValueError("rank-normalized ablation requires matching strategy")


def _engine(engine: str, repository, *, ablation: str = "weighted-rrf"):
    if engine == "legacy":
        return KnowledgeRetrievalService(repository, engine_version="compatibility-v1")
    service = HybridKnowledgeRetrievalService(
        repository,
        ExactTermLexicalRetriever(repository),
    )
    service.ENGINE_VERSION = _engine_version(engine, ablation)
    return service


def _engine_version(engine: str, ablation: str) -> str:
    if engine == "legacy":
        return "compatibility-v1"
    return f"hybrid-v2:{ablation}"


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _code_tree_sha256() -> str:
    digest = hashlib.sha256()
    for root in (ROOT / "app", ROOT / "scripts"):
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            body = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            digest.update(len(body).to_bytes(8, "big"))
            digest.update(body)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
