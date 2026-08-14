from __future__ import annotations

from pathlib import Path

import pytest

from app.application.knowledge.diagnostics_service import (
    DiagnosticCapacityExhausted,
    DiagnosticCapacityGuard,
    RagArtifactCatalog,
    RagDiagnosticsService,
)
from app.services.knowledge_eval_artifacts_v3 import (
    KnowledgeEvalArtifactV3,
    KnowledgeEvalPairedArtifactV3,
    RetrievalDiagnosticSnapshotV1,
    canonical_sha256,
    load_eval_artifact_v3,
    write_frozen_eval_artifact,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "eval" / "knowledge-v3" / "machine-preannotation"


def _single_case_tuning_artifact() -> KnowledgeEvalArtifactV3:
    source = load_eval_artifact_v3(FIXTURE_ROOT / "legacy-tuning-diagnostic.json")
    payload = source.model_dump(mode="json", exclude={"artifact_sha256"})
    payload["cases"] = payload["cases"][:1]
    payload["metrics"]["case_count"] = 1
    return KnowledgeEvalArtifactV3(
        **payload,
        artifact_sha256=canonical_sha256(payload),
    )


def _snapshot(
    artifact: KnowledgeEvalArtifactV3,
    *,
    artifact_sha256: str | None = None,
    case_id: str | None = None,
    title: str = "Safe diagnostic title",
) -> RetrievalDiagnosticSnapshotV1:
    case = artifact.cases[0]
    payload = {
        "schema_version": "retrieval-diagnostic-snapshot-v1",
        "created_at": artifact.created_at,
        "artifact_sha256": artifact_sha256 or artifact.artifact_sha256,
        "case_id": case_id or case.case_id,
        "request_id": "catalog-test-request",
        "trace_schema_version": "retrieval-trace-v3",
        "query_sha256": "a" * 64,
        "query_character_count": 18,
        "engine_version": artifact.identity.engine_version,
        "profile_id": artifact.identity.profile_id,
        "profile_version": artifact.identity.profile_version,
        "component_versions": {
            "corpus_manifest_sha256": artifact.identity.corpus_manifest_sha256,
        },
        "candidates": (
            {
                "chunk_id": "safe-candidate",
                "title": title,
                "domain": "redis",
                "topic": "",
                "source_type": "theory",
                "tags": (),
                "content_sha256": "b" * 64,
                "semantic_rank": None,
                "semantic_score": None,
                "lexical_rank": None,
                "lexical_score": None,
                "fusion_rank": None,
                "fusion_score": None,
                "rerank_rank": 1,
                "rerank_score": 0.9,
                "channel_hits": (),
                "matched_terms": (),
                "ranking_explanation": None,
                "selected": True,
            },
        ),
        "selected_evidence_ids": ("safe-candidate",),
        "evidence_decision": None,
        "latency_breakdown_ms": {
            "semantic": 1.0,
            "lexical": None,
            "fusion": None,
            "rerank": 0.5,
            "evidence_gate": 0.2,
            "total": 1.7,
        },
        "degraded_reasons": (),
    }
    return RetrievalDiagnosticSnapshotV1(
        **payload,
        snapshot_sha256=canonical_sha256(payload),
    )


def _catalog_with_artifact(tmp_path: Path):
    artifact_root = tmp_path / "artifacts"
    snapshot_root = tmp_path / "snapshots"
    artifact = _single_case_tuning_artifact()
    write_frozen_eval_artifact(artifact, artifact_root / "tuning.json")
    (artifact_root / "dataset.json").write_text(
        (FIXTURE_ROOT / "dataset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return artifact, RagArtifactCatalog((artifact_root,), snapshot_root), snapshot_root


def test_catalog_excludes_holdout_from_list_exact_load_and_paired_enumeration(
    tmp_path,
):
    artifact_root = tmp_path / "artifacts"
    holdout = load_eval_artifact_v3(
        FIXTURE_ROOT / "legacy-holdout-diagnostic.json"
    )
    write_frozen_eval_artifact(holdout, artifact_root / "sealed-holdout.json")

    paired_payload = {
        "schema_version": "knowledge-eval-paired-v3",
        "created_at": holdout.created_at,
        "dataset_version": holdout.dataset_version,
        "dataset_sha256": holdout.dataset_sha256,
        "corpus_manifest_sha256": holdout.identity.corpus_manifest_sha256,
        "split": "holdout",
        "baseline_artifact_sha256": holdout.artifact_sha256,
        "candidate_artifact_sha256": "c" * 64,
        "threshold_registration_sha256": "d" * 64,
        "comparison": {
            "split": "holdout",
            "baseline_engine_version": holdout.identity.engine_version,
            "candidate_engine_version": "sealed-candidate",
            "metrics": [],
            "case_type_deltas": {},
        },
        "thresholds_passed": True,
        "failed_thresholds": (),
        "case_ids": tuple(item.case_id for item in holdout.cases),
    }
    paired = KnowledgeEvalPairedArtifactV3(
        **paired_payload,
        artifact_sha256=canonical_sha256(paired_payload),
    )
    write_frozen_eval_artifact(paired, artifact_root / "sealed-paired.json")

    catalog = RagArtifactCatalog((artifact_root,), tmp_path / "snapshots")
    assert catalog.list().artifacts == ()
    assert catalog.paired().comparisons == ()
    with pytest.raises(KeyError, match="artifact not found"):
        catalog.load(holdout.artifact_sha256)


def test_machine_preannotation_holdout_is_visible_only_as_historical_diagnostic():
    catalog = RagArtifactCatalog()
    holdouts = [item for item in catalog.list().artifacts if item.split == "holdout"]

    assert len(holdouts) == 1
    assert holdouts[0].case_count == 25
    assert holdouts[0].holdout_status == "historical_diagnostic"
    assert holdouts[0].human_annotator_count == 0
    assert holdouts[0].independent_evidence_eligible is False
    assert catalog.load(holdouts[0].artifact_sha256).split == "holdout"


def test_damaged_snapshot_is_partial_and_fails_closed_without_retrieval_rerun(
    tmp_path,
):
    artifact, catalog, snapshot_root = _catalog_with_artifact(tmp_path)
    case_id = artifact.cases[0].case_id
    path = catalog.snapshot_path(artifact.artifact_sha256, case_id)
    path.parent.mkdir(parents=True)
    path.write_text('{"damaged": true}', encoding="utf-8")

    class RetrievalMustNotRun:
        def inspect_retrieval(self, *args, **kwargs):
            raise AssertionError("historical Snapshot access must never rerun retrieval")

    assert path.is_relative_to(snapshot_root.resolve())
    assert catalog.has_valid_snapshot(artifact.artifact_sha256, case_id) is False
    assert catalog.cases(artifact.artifact_sha256).cases[0].diagnostic_fidelity == (
        "partial_historical"
    )
    assert catalog.list().artifacts[0].diagnostic_fidelity == "partial_historical"
    with pytest.raises(ValueError):
        RagDiagnosticsService(
            repository=RetrievalMustNotRun(), catalog=catalog
        ).artifact_replay(artifact.artifact_sha256, case_id)


def test_snapshot_identity_mismatch_fails_closed(tmp_path):
    artifact, catalog, _ = _catalog_with_artifact(tmp_path)
    case_id = artifact.cases[0].case_id
    snapshot = _snapshot(artifact, artifact_sha256="e" * 64)
    write_frozen_eval_artifact(
        snapshot,
        catalog.snapshot_path(artifact.artifact_sha256, case_id),
    )

    assert catalog.has_valid_snapshot(artifact.artifact_sha256, case_id) is False
    with pytest.raises(ValueError, match="snapshot identity mismatch"):
        catalog.snapshot(artifact.artifact_sha256, case_id)


@pytest.mark.parametrize(
    ("artifact_sha256", "case_id"),
    (
        ("../" + "a" * 61, "case-1"),
        ("not-a-sha", "case-1"),
        ("a" * 64, "../private"),
        ("a" * 64, "case/child"),
    ),
)
def test_snapshot_path_rejects_malformed_and_traversal_references(
    tmp_path, artifact_sha256, case_id
):
    catalog = RagArtifactCatalog((), tmp_path / "snapshots")
    with pytest.raises(ValueError, match="invalid artifact reference"):
        catalog.snapshot_path(artifact_sha256, case_id)


def test_full_snapshot_requires_valid_hash_schema_and_identity(tmp_path):
    artifact, catalog, _ = _catalog_with_artifact(tmp_path)
    case_id = artifact.cases[0].case_id
    snapshot = _snapshot(artifact)
    write_frozen_eval_artifact(
        snapshot,
        catalog.snapshot_path(artifact.artifact_sha256, case_id),
    )

    assert catalog.has_valid_snapshot(artifact.artifact_sha256, case_id) is True
    assert catalog.cases(artifact.artifact_sha256).cases[0].diagnostic_fidelity == (
        "full_snapshot"
    )
    assert catalog.list().artifacts[0].diagnostic_fidelity == "full_snapshot"


def test_safe_snapshot_replay_never_exposes_knowledge_chunk_content(tmp_path):
    artifact, catalog, _ = _catalog_with_artifact(tmp_path)
    case_id = artifact.cases[0].case_id
    private_body = "PRIVATE KNOWLEDGE BODY MUST NEVER APPEAR"
    snapshot = _snapshot(artifact, title="Safe title only")
    write_frozen_eval_artifact(
        snapshot,
        catalog.snapshot_path(artifact.artifact_sha256, case_id),
    )

    response = RagDiagnosticsService(catalog=catalog).artifact_replay(
        artifact.artifact_sha256, case_id
    )
    rendered = response.model_dump_json()
    assert private_body not in rendered
    assert response.candidates[0].safe_excerpt == (
        "Frozen diagnostic metadata; full content is not exposed."
    )


def test_catalog_exposes_validated_dataset_without_private_method_coupling():
    catalog = RagArtifactCatalog()
    artifact = catalog.load(catalog.list().artifacts[0].artifact_sha256)

    dataset = catalog.dataset_for(artifact)

    assert dataset.version == artifact.dataset_version


def test_live_diagnostic_capacity_guard_fails_fast_and_recovers():
    guard = DiagnosticCapacityGuard(max_concurrency=1)
    assert guard.acquire() is True
    assert guard.acquire() is False
    guard.release()
    assert guard.acquire() is True
    guard.release()


def test_live_diagnostic_rejects_saturation_before_repository_call():
    class RetrievalMustNotRun:
        def inspect_retrieval(self, *args, **kwargs):
            raise AssertionError("saturated diagnostics must fail before retrieval")

    guard = DiagnosticCapacityGuard(max_concurrency=1)
    assert guard.acquire() is True
    service = RagDiagnosticsService(
        repository=RetrievalMustNotRun(),
        capacity_guard=guard,
    )
    from app.application.knowledge.diagnostic_models import RetrievalInspectionRequest

    try:
        with pytest.raises(DiagnosticCapacityExhausted):
            service.inspect(RetrievalInspectionRequest(query_text="Redis"))
    finally:
        guard.release()
