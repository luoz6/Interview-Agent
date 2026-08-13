from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
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
    build_threshold_registration_v3,
    canonical_sha256,
    compare_knowledge_eval_artifacts_v3,
    evaluate_knowledge_engine_v3,
    load_eval_artifact_v3,
    load_threshold_registration_v3,
    validate_registered_candidate_v3,
    write_frozen_eval_artifact,
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
            "Template, validate, run, pre-register, and compare "
            "privacy-safe Knowledge Eval V3 artifacts"
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
    run.add_argument("--thresholds", type=Path)
    run.add_argument("--split", choices=("tuning", "holdout"), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--code-revision")
    run.add_argument("--vector-validity-rate", type=float, default=1.0)

    template = subparsers.add_parser("template")
    template.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    template.add_argument("--output", type=Path, required=True)
    template.add_argument("--catalog-output", type=Path, required=True)

    register = subparsers.add_parser("register-thresholds")
    register.add_argument("--baseline", type=Path, required=True)
    register.add_argument("--policy", type=Path, required=True)
    register.add_argument(
        "--candidate-ablation",
        choices=ABLATION_CHOICES,
        default="weighted-rrf",
    )
    register.add_argument("--candidate-profile", type=Path)
    register.add_argument("--candidate-code-revision")
    register.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--thresholds", type=Path)
    compare.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "template":
        manifest = _load_manifest(args.manifest)
        template_artifact, catalog = _annotation_template(manifest)
        _write_frozen_json(template_artifact, args.output)
        try:
            _write_frozen_json(catalog, args.catalog_output)
        except Exception:
            args.output.unlink(missing_ok=True)
            raise
        print(
            json.dumps(
                {
                    "annotation_template": str(args.output),
                    "chunk_catalog": str(args.catalog_output),
                    "chunk_count": len(catalog["chunks"]),
                },
                sort_keys=True,
            )
        )
        return 0
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
        registration = (
            load_threshold_registration_v3(args.thresholds)
            if args.thresholds is not None
            else None
        )
        paired = compare_knowledge_eval_artifacts_v3(
            baseline,
            load_eval_artifact_v3(args.candidate),
            threshold_registration=registration,
        )
        write_frozen_eval_artifact(paired, args.output)
        print(
            json.dumps(
                {
                    "artifact": str(args.output),
                    "artifact_sha256": paired.artifact_sha256,
                    "split": paired.split,
                    "thresholds_passed": paired.thresholds_passed,
                    "failed_thresholds": list(paired.failed_thresholds),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "register-thresholds":
        baseline = load_eval_artifact_v3(args.baseline)
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        candidate_profile = _profile(
            "hybrid-v2",
            ablation=args.candidate_ablation,
            profile_path=args.candidate_profile,
        )
        registration = build_threshold_registration_v3(
            baseline,
            primary_metric=policy["primary_metric"],
            minimum_deltas=policy["minimum_deltas"],
            maximum_deltas=policy["maximum_deltas"],
            absolute_minimums=policy["absolute_minimums"],
            absolute_maximums=policy["absolute_maximums"],
            profile_p95_budgets_ms=policy["profile_p95_budgets_ms"],
            profile_p95_relative_limits=policy["profile_p95_relative_limits"],
            candidate_engine_version=_engine_version(
                "hybrid-v2", args.candidate_ablation
            ),
            candidate_code_revision=(
                args.candidate_code_revision or _git_revision()
            ),
            candidate_code_tree_sha256=_code_tree_sha256(),
            candidate_profile=candidate_profile,
            rationale_record_sha256=policy["rationale_record_sha256"],
        )
        write_frozen_eval_artifact(registration, args.output)
        print(
            json.dumps(
                {
                    "artifact": str(args.output),
                    "registration_sha256": registration.registration_sha256,
                },
                sort_keys=True,
            )
        )
        return 0

    dataset, manifest = _load_dataset(args.dataset, args.manifest)
    if args.engine == "legacy" and (args.ablation is not None or args.profile is not None):
        raise ValueError("Legacy evaluation does not accept Hybrid ablation/profile")
    ablation = args.ablation or "weighted-rrf"
    if args.split == "holdout" and args.engine == "hybrid-v2" and args.thresholds is None:
        raise ValueError(
            "Hybrid holdout run requires a pre-registered threshold artifact"
        )
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
        if args.split == "holdout" and args.engine == "hybrid-v2":
            registration = load_threshold_registration_v3(args.thresholds)
            if registration.registered_at >= datetime.now(timezone.utc):
                raise ValueError(
                    "threshold registration must predate the Hybrid holdout run"
                )
            if registration.dataset_version != dataset.version:
                raise ValueError("threshold registration has different dataset_version")
            if registration.dataset_sha256 != canonical_sha256(
                dataset.model_dump(mode="json")
            ):
                raise ValueError("threshold registration has different dataset_sha256")
            if (
                registration.corpus_manifest_sha256
                != str(manifest["corpus_manifest_sha256"])
            ):
                raise ValueError(
                    "threshold registration has different corpus_manifest_sha256"
                )
            validate_registered_candidate_v3(registration, identity)
        artifact = evaluate_knowledge_engine_v3(
            dataset,
            engine,
            repository,
            split=args.split,
            profile=profile,
            identity=identity,
            vector_validity_rate=args.vector_validity_rate,
        )
        write_frozen_eval_artifact(artifact, args.output)
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
        require_release_shape=True,
    )
    return dataset, manifest


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _annotation_template(manifest: dict) -> tuple[dict, dict]:
    manifest_sha = str(manifest["corpus_manifest_sha256"])
    template = {
        "schema_version": "knowledge-eval-v3-annotation-template",
        "dataset": {
            "version": "REPLACE_WITH_FROZEN_DATASET_VERSION",
            "corpus_manifest_sha256": manifest_sha,
            "governance": {
                "annotation_protocol_version": "REPLACE_ME",
                "annotator_role": "REPLACE_ME",
                "minimum_annotators_per_case": 2,
                "implementation_output_blinded": True,
                "split_frozen": True,
                "agreement_metric": "REPLACE_ME",
                "agreement_value": None,
                "minimum_agreement": None,
                "labeling_started_at": "REPLACE_WITH_TIMEZONE_AWARE_TIMESTAMP",
                "split_frozen_at": "REPLACE_WITH_TIMEZONE_AWARE_TIMESTAMP",
                "provenance_record_sha256": "REPLACE_WITH_SHA256",
            },
            "cases": [],
        },
        "case_schema": {
            "case_id": "",
            "case_family": "",
            "case_type": "",
            "split": "tuning_or_holdout",
            "evaluation_group": "",
            "query_text": "",
            "canonical_tags": [],
            "source_types": [],
            "allowed_domains": [],
            "primary_relevant_chunk_ids": [],
            "accepted_related_chunk_ids": [],
            "excluded_chunk_ids": [],
            "annotator_identity_sha256s": [],
            "annotation_record_sha256s": [],
            "label_consensus_record_sha256": None,
            "expected_no_evidence": False,
            "top_k": 5,
        },
        "rules": [
            "Create 80-120 independently labeled cases; do not copy engine output.",
            "Freeze case-family split before any engine evaluation.",
            "Keep each case family entirely within tuning or holdout.",
            "Use at least two blinded annotators and retain hashed records.",
        ],
    }
    catalog_fields = (
        "chunk_id",
        "title",
        "domain",
        "source_type",
        "tags",
        "aliases",
        "content_kind",
    )
    catalog = {
        "schema_version": "knowledge-eval-v3-chunk-catalog",
        "corpus_version": manifest["corpus_version"],
        "corpus_manifest_sha256": manifest_sha,
        "chunks": [
            {key: item[key] for key in catalog_fields if key in item}
            for item in manifest.get("chunks", [])
        ],
    }
    return template, catalog


def _write_frozen_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite frozen eval artifact: {path}") from exc


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
