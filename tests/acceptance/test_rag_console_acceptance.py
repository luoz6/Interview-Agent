from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import sleep

import pytest
from fastapi.testclient import TestClient

from app.api.shared.dependencies import get_rag_diagnostics_service
from app.application.knowledge.diagnostics_service import (
    DiagnosticCapacityGuard,
    RagArtifactCatalog,
    RagDiagnosticsService,
)
from app.application.knowledge.diagnostic_models import RetrievalCompareRequest
from app.domain.knowledge.evidence_gate import RetrievalEvidenceGate
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelTrace,
    RetrievalResult,
    RetrievalRerankSummary,
    build_retrieval_trace,
)
from app.main import app
from app.runtime.config import use_environment
from app.services.knowledge_eval_artifacts_v3 import (
    RetrievalDiagnosticSnapshotV1,
    canonical_sha256,
    load_eval_artifact_v3,
    write_frozen_eval_artifact,
)


CONSOLE_ENV = {
    "RAG_DIAGNOSTIC_UI_ENABLED": "true",
    "RAG_LIVE_INSPECTOR_ENABLED": "true",
    "RAG_EVAL_ARTIFACT_ACCESS_ENABLED": "true",
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MACHINE_ARTIFACT_ROOT = REPOSITORY_ROOT / "eval" / "knowledge-v3" / "machine-preannotation"


class DeterministicDiagnosticRepository:
    def __init__(self) -> None:
        self.calls = 0

    def inspect_retrieval(self, request, *, profile, engine):
        self.calls += 1
        chunk = KnowledgeChunk(
            chunk_id="redis-lock",
            title="Redis distributed lock",
            content="Private source body that must not cross the diagnostic DTO.",
            source_type="theory",
            domain="redis",
            tags=["redis", "locking"],
            metadata={
                "candidate_summary": "Safe summary for a deterministic acceptance case.",
                "topic": "locking",
                "content_sha256": "b" * 64,
                "corpus_manifest_sha256": "c" * 64,
                "corpus_version": "acceptance-v1",
            },
            score=0.9,
        )
        candidate = RetrievalCandidate(
            chunk=chunk,
            semantic_rank=1,
            semantic_score=0.9,
            fusion_rank=1,
            fusion_score=0.03,
            rerank_rank=1,
            rerank_score=0.9,
            channel_hits=["semantic"],
        )
        decision = RetrievalEvidenceGate().decide_selection(
            RetrievalAvailability.AVAILABLE,
            [chunk],
        )
        trace = build_retrieval_trace(
            request=request,
            profile=profile,
            channels=[
                RetrievalChannelTrace(
                    channel="semantic",
                    status="completed",
                    latency_ms=1.0,
                    candidate_count=1,
                    hit_ids=[chunk.chunk_id],
                )
            ],
            selected_evidence=[chunk],
            degraded_reasons=[],
            latency_ms=2.0,
            latency_breakdown_ms={
                "semantic": 1.0,
                "lexical": None,
                "fusion": 0.1,
                "rerank": 0.2,
                "evidence_gate": 0.1,
                "total": 2.0,
            },
            fusion_summary=None,
            rerank_summary=RetrievalRerankSummary(
                strategy_version="deterministic-v1",
                input_candidate_count=1,
                selected_count=1,
                candidate_limit=1,
                evidence_limit=1,
                minimum_score=0,
            ),
            evidence_decision=decision,
            component_versions={
                "retrieval_engine_version": engine,
                "corpus_manifest_sha256": "c" * 64,
            },
        )
        return RetrievalResult(
            request_id=request.request_id,
            availability=RetrievalAvailability.AVAILABLE,
            candidates=[candidate],
            selected_evidence=[chunk],
            evidence_decision=decision,
            trace=trace,
            retrieval_engine_version=engine,
            profile_version=profile.profile_version,
            latency_ms=2.0,
        )


class EmptyTraceStore:
    def get(self, trace_id):
        return {"trace_id": trace_id, "plan": None}

    def list_question_evaluations(self, trace_id):
        return ()


@pytest.fixture(autouse=True)
def _clear_rag_override():
    yield
    app.dependency_overrides.pop(get_rag_diagnostics_service, None)


def _client(service):
    app.dependency_overrides[get_rag_diagnostics_service] = lambda: service
    return TestClient(app, client=("127.0.0.1", 51000))


def test_default_off_and_learning_demo_runtime_remains_explicit_legacy():
    client = TestClient(app, client=("127.0.0.1", 51000))
    assert client.get("/api/rag/overview").status_code == 404
    assert client.post(
        "/api/rag/inspections", json={"query_text": "Redis"}
    ).status_code == 404
    assert client.get("/api/rag/evaluations").status_code == 404
    assert client.get("/api/rag/inspections/opaque-id").status_code == 404

    service = RagDiagnosticsService()
    with use_environment({"RAG_DIAGNOSTIC_UI_ENABLED": "true"}):
        overview = _client(service).get("/api/rag/overview")

    assert overview.status_code == 200
    body = overview.json()
    assert body["current_engine"] == "legacy"
    assert body["project_scope"] == "learning_project_technical_showcase"
    assert body["comparison_engines"] == ["legacy", "hybrid-v2"]
    assert body["remote_reranker_enabled"] is False
    assert body["diagnostic_dataset"]["label"] == "Demo Diagnostic Dataset"
    assert body["diagnostic_dataset"]["production_claim"] is False
    assert not {"promotion", "shadow_enabled", "hybrid_rollout_percent"}.intersection(body)


def test_live_inspection_is_synchronous_safe_and_uses_fixed_local_repository():
    repository = DeterministicDiagnosticRepository()
    client = _client(RagDiagnosticsService(repository=repository))
    private_query = "Explain Redis locking without leaking this query"

    with use_environment(CONSOLE_ENV):
        response = client.post(
            "/api/rag/inspections",
            json={
                "query_text": private_query,
                "intent": "question_review",
                "profile_id": "question-review",
                "engine": "hybrid-v2",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert repository.calls == 1
    assert body["mode"] == "live"
    assert body["candidates"][0]["safe_excerpt"].startswith("Safe summary")
    assert private_query not in response.text
    assert "Private source body" not in response.text
    assert body["consumer_action"]["recording_status"] == "not_recorded"


def test_compare_runs_both_engines_in_one_safe_request_and_returns_server_diff():
    repository = DeterministicDiagnosticRepository()
    client = _client(RagDiagnosticsService(repository=repository))
    private_query = "PRIVATE compare query must never be reflected"

    with use_environment(CONSOLE_ENV):
        response = client.post(
            "/api/rag/inspections/compare",
            json={
                "query_text": private_query,
                "intent": "question_review",
                "profile_id": "question-review",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert repository.calls == 2
    assert body["legacy"]["status"] == "success"
    assert body["hybrid"]["status"] == "success"
    assert body["top_k_overlap"]["candidate_ids"] == ["redis-lock"]
    assert body["selected_evidence_changed"] is False
    assert body["evidence_decision_changed"] is False
    assert body["corpus_manifest_sha256"] == "c" * 64
    assert private_query not in response.text
    assert "Private source body" not in response.text


def test_compare_isolates_one_engine_failure_without_leaking_exception_detail():
    class OneSideFailureRepository(DeterministicDiagnosticRepository):
        def inspect_retrieval(self, request, *, profile, engine):
            if engine == "hybrid-v2":
                self.calls += 1
                raise RuntimeError("PRIVATE provider failure detail")
            return super().inspect_retrieval(request, profile=profile, engine=engine)

    repository = OneSideFailureRepository()
    client = _client(RagDiagnosticsService(repository=repository))
    with use_environment(CONSOLE_ENV):
        response = client.post(
            "/api/rag/inspections/compare",
            json={"query_text": "private failure query", "intent": "question_review"},
        )

    assert response.status_code == 200
    body = response.json()
    assert repository.calls == 2
    assert body["legacy"]["status"] == "success"
    assert body["hybrid"] == {
        "status": "failed",
        "inspection": None,
        "failure_code": "retrieval_failed",
    }
    assert body["top_k_overlap"] is None
    assert "PRIVATE provider failure detail" not in response.text
    assert "private failure query" not in response.text


def test_compare_timeout_is_bounded_and_does_not_discard_successful_side():
    class SlowHybridRepository(DeterministicDiagnosticRepository):
        def inspect_retrieval(self, request, *, profile, engine):
            if engine == "hybrid-v2":
                sleep(0.05)
            return super().inspect_retrieval(request, profile=profile, engine=engine)

    guard = DiagnosticCapacityGuard(max_concurrency=1)
    service = RagDiagnosticsService(
        repository=SlowHybridRepository(),
        capacity_guard=guard,
        compare_timeout_seconds=0.01,
    )
    response = service.compare(
        RetrievalCompareRequest(
            query_text="bounded compare",
            intent="question_review",
        )
    )

    assert response.legacy.status == "success"
    assert response.hybrid.status == "timeout"
    assert response.hybrid.failure_code == "retrieval_timeout"
    assert response.top_k_overlap is None
    assert guard.acquire() is False
    sleep(0.06)
    assert guard.acquire() is True
    guard.release()


def test_compare_rejects_mixed_corpus_identity_without_reflecting_query():
    class MixedIdentityRepository(DeterministicDiagnosticRepository):
        def inspect_retrieval(self, request, *, profile, engine):
            result = super().inspect_retrieval(request, profile=profile, engine=engine)
            if engine == "hybrid-v2":
                result.candidates[0].chunk.metadata["corpus_manifest_sha256"] = "d" * 64
            return result

    client = _client(RagDiagnosticsService(repository=MixedIdentityRepository()))
    with use_environment(CONSOLE_ENV):
        response = client.post(
            "/api/rag/inspections/compare",
            json={"query_text": "private mixed identity query", "intent": "question_review"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RAG_COMPARE_IDENTITY_CONFLICT"
    assert "private mixed identity query" not in response.text


def test_artifact_catalog_replay_is_provider_free_and_historical_holdout_is_not_sealed():
    class RetrievalMustNotRun:
        def inspect_retrieval(self, *args, **kwargs):
            raise AssertionError("artifact replay must not call retrieval")

    service = RagDiagnosticsService(repository=RetrievalMustNotRun())
    client = _client(service)
    catalog = service.evaluations()
    historical = next(item for item in catalog.artifacts if item.split == "holdout")
    tuning = next(item for item in catalog.artifacts if item.split == "tuning")
    case = service.evaluation_cases(tuning.artifact_sha256).cases[0]

    with use_environment(CONSOLE_ENV):
        listed = client.get("/api/rag/evaluations")
        replay = client.get(
            f"/api/rag/evaluations/{tuning.artifact_sha256}/cases/"
            f"{case.case_id}/diagnostic-snapshot"
        )

    assert listed.status_code == 200
    assert len(listed.json()["artifacts"]) == 6
    assert historical.case_count == 25
    assert historical.holdout_status == "historical_diagnostic"
    assert historical.independent_evidence_eligible is False
    assert replay.status_code == 200
    assert replay.json()["diagnostic_fidelity"] == "partial_historical"
    assert replay.json()["provider_call_possible"] is False
    assert replay.json()["trace_schema_version"] == "not_recorded"


def test_valid_snapshot_supports_full_replay_without_retrieval(tmp_path):
    class RetrievalMustNotRun:
        calls = 0

        def inspect_retrieval(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("full snapshot replay must not call retrieval")

    repository = RetrievalMustNotRun()
    catalog = RagArtifactCatalog(snapshot_root=tmp_path / "snapshots")
    artifact = catalog.load(catalog.list().artifacts[0].artifact_sha256)
    case = artifact.cases[0]
    payload = {
        "schema_version": "retrieval-diagnostic-snapshot-v1",
        "created_at": datetime(2026, 8, 13, tzinfo=timezone.utc),
        "artifact_sha256": artifact.artifact_sha256,
        "case_id": case.case_id,
        "request_id": "acceptance-snapshot",
        "trace_schema_version": "retrieval-trace-v3",
        "query_sha256": "a" * 64,
        "query_character_count": 17,
        "engine_version": artifact.identity.engine_version,
        "profile_id": artifact.identity.profile_id,
        "profile_version": artifact.identity.profile_version,
        "component_versions": {
            "corpus_manifest_sha256": artifact.identity.corpus_manifest_sha256
        },
        "candidates": ({
            "chunk_id": "safe-candidate",
            "title": "Safe frozen candidate",
            "domain": "redis",
            "topic": "locking",
            "source_type": "theory",
            "tags": ("redis",),
            "content_sha256": "b" * 64,
            "semantic_rank": 1,
            "semantic_score": 0.9,
            "lexical_rank": 2,
            "lexical_score": 3.5,
            "fusion_rank": 1,
            "fusion_score": 0.03,
            "rerank_rank": 1,
            "rerank_score": 0.09,
            "channel_hits": ("semantic", "lexical"),
            "matched_terms": ("redis",),
            "ranking_explanation": {
                "base_score_source": "fusion_score",
                "base_score": 0.03,
                "exact_term_boost": 0.06,
                "routing_tag_boost": 0.0,
                "eligibility_score": 0.96,
                "eligible": True,
                "final_rerank_score": 0.09,
                "tie_break_fusion_rank": 1,
                "reason_codes": ("eligible", "exact_term_boost"),
            },
            "selected": True,
        },),
        "selected_evidence_ids": ("safe-candidate",),
        "evidence_decision": {
            "availability": "available",
            "sufficiency": "sufficient",
            "consistency": "consistent",
            "evaluation_confidence": "high",
            "covered_signals": ("redis",),
            "missing_signals": (),
            "reason_codes": ("evidence_sufficient",),
            "gate_version": "acceptance-gate-v1",
        },
        "latency_breakdown_ms": {
            "semantic": 1.0,
            "lexical": 0.8,
            "fusion": 0.1,
            "rerank": 0.2,
            "evidence_gate": 0.1,
            "total": 2.0,
        },
        "degraded_reasons": (),
    }
    snapshot = RetrievalDiagnosticSnapshotV1(
        **payload,
        snapshot_sha256=canonical_sha256(payload),
    )
    write_frozen_eval_artifact(
        snapshot,
        catalog.snapshot_path(artifact.artifact_sha256, case.case_id),
    )
    client = _client(RagDiagnosticsService(catalog=catalog, repository=repository))

    with use_environment(CONSOLE_ENV):
        response = client.get(
            f"/api/rag/evaluations/{artifact.artifact_sha256}/cases/"
            f"{case.case_id}/diagnostic-snapshot"
        )

    assert response.status_code == 200
    body = response.json()
    assert repository.calls == 0
    assert body["diagnostic_fidelity"] == "full_snapshot"
    assert body["provider_call_possible"] is False
    candidate = body["candidates"][0]
    assert candidate["semantic_rank"] == 1
    assert candidate["lexical_rank"] == 2
    assert candidate["fusion_rank"] == 1
    assert candidate["rerank_rank"] == 1
    assert candidate["selected"] is True
    assert candidate["channel_hits"] == ["semantic", "lexical"]
    assert candidate["matched_terms"] == ["redis"]
    assert candidate["ranking_explanation"]["base_score_source"] == "fusion_score"
    assert candidate["ranking_explanation"]["final_rerank_score"] == 0.09
    assert body["evidence_decision"]["sufficiency"] == "sufficient"
    assert body["evidence_decision"]["gate_version"] == "acceptance-gate-v1"
    assert body["latency_ms"] == {
        "semantic": 1.0,
        "lexical": 0.8,
        "fusion": 0.1,
        "rerank": 0.2,
        "evidence_gate": 0.1,
        "total": 2.0,
    }


def test_artifact_detail_returns_safe_dto_and_invalid_identity_fails_closed():
    service = RagDiagnosticsService()
    client = _client(service)
    artifact = service.evaluations().artifacts[0]

    with use_environment(CONSOLE_ENV):
        detail = client.get(f"/api/rag/evaluations/{artifact.artifact_sha256}")
        invalid_sha = client.get("/api/rag/evaluations/not-a-sha")
        path_like = client.get(
            "/api/rag/evaluations/..%2Fprivate-marker%2Fsealed.json"
        )

    assert detail.status_code == 200
    body = detail.json()
    assert body["schema_version"] == "rag-artifact-detail-v1"
    assert body["artifact"]["artifact_sha256"] == artifact.artifact_sha256
    assert isinstance(body["paired_comparisons"], list)
    for forbidden in (
        "query_text",
        "content",
        "source_url",
        "provider_payload",
        "filesystem_path",
    ):
        assert f'"{forbidden}"' not in detail.text

    for rejected in (invalid_sha, path_like):
        assert rejected.status_code == 404
        assert "private-marker" not in rejected.text
        assert "sealed.json" not in rejected.text
        assert "Interview-Agent" not in rejected.text


def test_private_holdout_and_sensitive_evidence_fields_have_no_public_path(tmp_path):
    source = load_eval_artifact_v3(
        MACHINE_ARTIFACT_ROOT / "legacy-holdout-diagnostic.json"
    )
    root = tmp_path / "private"
    write_frozen_eval_artifact(source, root / "sealed.json")
    service = RagDiagnosticsService(
        catalog=RagArtifactCatalog(
            roots=(root,),
            snapshot_root=tmp_path / "snapshots",
            historical_holdout_roots=(),
        ),
        session_store=EmptyTraceStore(),
    )
    client = _client(service)

    with use_environment(CONSOLE_ENV):
        listed = client.get("/api/rag/evaluations")
        direct = client.get(f"/api/rag/evaluations/{source.artifact_sha256}")
        trace = client.get("/api/rag/evidence-traces/opaque-trace")

    assert listed.status_code == 200
    assert listed.json()["artifacts"] == []
    assert direct.status_code == 404
    assert trace.status_code == 200
    rendered = trace.text
    for forbidden in (
        "query_text",
        "resume",
        "job_description",
        "provider_payload",
        "chain_of_thought",
    ):
        assert f'"{forbidden}"' not in rendered
    assert all(
        stage["recording_status"] == "not_recorded"
        for stage in trace.json()["stages"]
    )
