from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalResult,
    RetrievalTrace,
    SanitizedRetrievalQueryFacts,
)
from app.ports.runtime import KnowledgeLookupResult
from app.services.knowledge_eval_artifacts_v3 import (
    KnowledgeEvalArtifactV3,
    build_engine_identity_v3,
    canonical_sha256,
    compare_knowledge_eval_artifacts_v3,
    evaluate_knowledge_engine_v3,
    load_eval_artifact_v3,
    write_frozen_eval_artifact,
    write_retrieval_diagnostic_snapshots_v1,
    load_retrieval_diagnostic_snapshot_v1,
)
from app.services.knowledge_eval_dataset_v3 import (
    KnowledgeRetrievalCaseV3,
    KnowledgeRetrievalDatasetV3,
)


NOW = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)


def _case(**overrides):
    payload = {
        "case_id": "redis-lock",
        "case_family": "redis-lock-owner",
        "case_type": "exact_technical_term",
        "split": "holdout",
        "evaluation_group": "redis",
        "query_text": "Redis 分布式锁怎样安全释放？",
        "canonical_tags": ["redis"],
        "source_types": ["theory"],
        "allowed_domains": ["redis"],
        "primary_relevant_chunk_ids": ["lock"],
        "accepted_related_chunk_ids": [],
        "excluded_chunk_ids": ["cache"],
    }
    payload.update(overrides)
    return KnowledgeRetrievalCaseV3(**payload)


def _dataset():
    return KnowledgeRetrievalDatasetV3(
        version="eval-v3-test",
        corpus_manifest_sha256="a" * 64,
        cases=[
            _case(
                case_id="tuning-lock",
                case_family="tuning-lock-owner",
                split="tuning",
                query_text="锁持有者令牌如何校验？",
            ),
            _case(),
            _case(
                case_id="no-evidence",
                case_family="unknown-protocol",
                case_type="no_evidence",
                query_text="不存在的缓存一致性协议是什么？",
                primary_relevant_chunk_ids=[],
                accepted_related_chunk_ids=[],
                expected_no_evidence=True,
            ),
        ],
    )


def _chunk():
    return KnowledgeChunk(
        chunk_id="lock",
        title="Redis lock",
        content="PRIVATE KNOWLEDGE BODY",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={
            "content_sha256": "b" * 64,
            "corpus_manifest_sha256": "a" * 64,
        },
        score=0.9,
    )


class Repository:
    embedding_provider = type(
        "Provider",
        (),
        {
            "provider_name": "fake-provider",
            "model_name": "fake-model",
            "model_revision": "fake-revision",
            "dimension": 1024,
        },
    )()

    def get_by_ids(self, ids, *, expected_hashes=None):
        return KnowledgeLookupResult(found=[_chunk()] if ids == ["lock"] else [])


class Engine:
    ENGINE_VERSION = "compatibility-v1"

    def retrieve(self, request, profile):
        if "不存在" in request.query_text:
            return RetrievalResult(
                request_id=request.request_id,
                availability=RetrievalAvailability.AVAILABLE,
                trace=RetrievalTrace(
                    request_id=request.request_id,
                    profile_id=profile.profile_id,
                    profile_version=profile.profile_version,
                    sanitized_query_facts=SanitizedRetrievalQueryFacts(
                        query_sha256="d" * 64,
                        character_count=len(request.query_text),
                    ),
                    latency_ms=2,
                ),
                retrieval_engine_version=self.ENGINE_VERSION,
                profile_version=profile.profile_version,
                latency_ms=2,
            )
        chunk = _chunk()
        return RetrievalResult(
            request_id=request.request_id,
            availability=RetrievalAvailability.AVAILABLE,
            candidates=[
                RetrievalCandidate(
                    chunk=chunk,
                    semantic_score=0.9,
                    semantic_rank=1,
                    rerank_score=0.9,
                    rerank_rank=1,
                    channel_hits=["semantic"],
                )
            ],
            selected_evidence=[chunk],
            trace=RetrievalTrace(
                request_id=request.request_id,
                profile_id=profile.profile_id,
                profile_version=profile.profile_version,
                sanitized_query_facts=SanitizedRetrievalQueryFacts(
                    query_sha256="e" * 64,
                    character_count=len(request.query_text),
                ),
                latency_ms=4,
            ),
            retrieval_engine_version=self.ENGINE_VERSION,
            profile_version=profile.profile_version,
            latency_ms=4,
        )


PROFILE = ResolvedRetrievalProfile(
    profile_id="eval-legacy-v3",
    profile_version="legacy-v1",
    evidence_limit=5,
)


def _artifact(
    engine_version="compatibility-v1",
    *,
    created_at=NOW,
    profile=PROFILE,
):
    engine = Engine()
    engine.ENGINE_VERSION = engine_version
    identity = build_engine_identity_v3(
        engine_version=engine_version,
        code_revision="deadbeef",
        code_tree_sha256="c" * 64,
        profile=profile,
        repository=Repository(),
        corpus_version="corpus-v1",
        corpus_manifest_sha256="a" * 64,
    )
    return evaluate_knowledge_engine_v3(
        _dataset(),
        engine,
        Repository(),
        split="holdout",
        profile=profile,
        identity=identity,
        created_at=created_at,
    )


def test_v3_eval_artifact_freezes_identity_per_case_rank_score_and_replay():
    artifact = _artifact()

    assert artifact.metrics.observation_completeness_rate == 1.0
    assert artifact.metrics.evidence_replay_stability_rate == 1.0
    assert artifact.identity.embedding_dimension == 1024
    assert artifact.cases[0].candidates[0].rank == 1
    assert artifact.cases[0].candidates[0].score == 0.9
    assert artifact.cases[0].replayed_evidence_ids == ("lock",)
    serialized = artifact.model_dump_json()
    assert "Redis 分布式锁怎样安全释放" not in serialized
    assert "PRIVATE KNOWLEDGE BODY" not in serialized


def test_frozen_writer_refuses_overwrite_and_loader_detects_tampering(tmp_path):
    artifact = _artifact()
    path = tmp_path / "legacy-holdout.json"

    write_frozen_eval_artifact(artifact, path)
    assert load_eval_artifact_v3(path) == artifact
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_frozen_eval_artifact(artifact, path)

    payload = artifact.model_dump(mode="json")
    payload["cases"][0]["latency_ms"] = 999
    with pytest.raises(ValidationError, match="SHA-256 mismatch"):
        KnowledgeEvalArtifactV3.model_validate(payload)


def test_snapshot_sidecars_use_the_same_eval_results_and_publish_atomically(tmp_path):
    results = {}
    engine = Engine()
    identity = build_engine_identity_v3(
        engine_version=engine.ENGINE_VERSION,
        code_revision="deadbeef",
        code_tree_sha256="c" * 64,
        profile=PROFILE,
        repository=Repository(),
        corpus_version="corpus-v1",
        corpus_manifest_sha256="a" * 64,
    )
    artifact = evaluate_knowledge_engine_v3(
        _dataset(),
        engine,
        Repository(),
        split="holdout",
        profile=PROFILE,
        identity=identity,
        created_at=NOW,
        result_observer=lambda case_id, result: results.__setitem__(case_id, result),
    )

    target = write_retrieval_diagnostic_snapshots_v1(artifact, results, tmp_path)

    assert target.name == artifact.artifact_sha256
    snapshot = load_retrieval_diagnostic_snapshot_v1(target / "redis-lock.json")
    assert snapshot.artifact_sha256 == artifact.artifact_sha256
    assert snapshot.selected_evidence_ids == ("lock",)
    assert "PRIVATE KNOWLEDGE BODY" not in snapshot.model_dump_json()
    assert not (tmp_path / f".{artifact.artifact_sha256}.staging").exists()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_retrieval_diagnostic_snapshots_v1(artifact, results, tmp_path)


def test_paired_artifact_requires_identical_dataset_corpus_split_and_cases():
    baseline = _artifact("compatibility-v1")
    hybrid_profile = PROFILE.model_copy(update={"profile_id": "eval-hybrid-v3"})
    candidate = _artifact(
        "hybrid-v2",
        created_at=datetime(2026, 8, 12, 10, tzinfo=timezone.utc),
        profile=hybrid_profile,
    )
    paired = compare_knowledge_eval_artifacts_v3(
        baseline,
        candidate,
        created_at=datetime(2026, 8, 12, 11, tzinfo=timezone.utc),
    )

    assert paired.baseline_artifact_sha256 == baseline.artifact_sha256
    assert paired.candidate_artifact_sha256 == candidate.artifact_sha256
    assert paired.case_ids == ("redis-lock", "no-evidence")
    assert paired.thresholds_passed is None
    assert paired.failed_thresholds == ()

    changed = candidate.model_copy(
        update={"dataset_sha256": "d" * 64},
    )
    with pytest.raises(ValueError, match="dataset_sha256"):
        compare_knowledge_eval_artifacts_v3(
            baseline,
            changed,
        )


def test_eval_ignores_unselected_raw_candidates_and_degraded_empty_is_not_true_empty():
    class RawOnlyEngine(Engine):
        availability = RetrievalAvailability.DEGRADED

        def retrieve(self, request, profile):
            chunk = _chunk()
            return RetrievalResult(
                request_id=request.request_id,
                    availability=self.availability,
                candidates=[
                    RetrievalCandidate(
                        chunk=chunk,
                        semantic_score=0.9,
                        semantic_rank=1,
                        channel_hits=["semantic"],
                    )
                ],
                selected_evidence=[],
                trace=RetrievalTrace(
                    request_id=request.request_id,
                    profile_id=profile.profile_id,
                    profile_version=profile.profile_version,
                    latency_ms=4,
                ),
                retrieval_engine_version=self.ENGINE_VERSION,
                profile_version=profile.profile_version,
                latency_ms=4,
            )

    identity = build_engine_identity_v3(
        engine_version="compatibility-v1",
        code_revision="deadbeef",
        code_tree_sha256="c" * 64,
        profile=PROFILE,
        repository=Repository(),
        corpus_version="corpus-v1",
        corpus_manifest_sha256="a" * 64,
    )
    artifact = evaluate_knowledge_engine_v3(
        _dataset(),
        RawOnlyEngine(),
        Repository(),
        split="holdout",
        profile=PROFILE,
        identity=identity,
        created_at=NOW,
    )

    assert artifact.cases[0].candidates == ()
    assert artifact.cases[0].declared_no_evidence is False

    RawOnlyEngine.availability = RetrievalAvailability.AVAILABLE
    abstained = evaluate_knowledge_engine_v3(
        _dataset(),
        RawOnlyEngine(),
        Repository(),
        split="holdout",
        profile=PROFILE,
        identity=identity,
        created_at=NOW,
    )

    assert abstained.cases[0].candidates == ()
    assert abstained.cases[0].declared_no_evidence is True
    assert artifact.metrics.recall_at_5 == 0.0


def test_canonical_hash_is_order_independent_and_timestamp_normalized():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256(
        {"a": 1, "b": 2}
    )
    assert canonical_sha256(NOW) == canonical_sha256(
        datetime(
            2026,
            8,
            12,
            16,
            tzinfo=timezone(timedelta(hours=8)),
        )
    )
