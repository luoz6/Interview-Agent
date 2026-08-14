from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.application.knowledge.runtime_retrieval_service import (
    RuntimeKnowledgeRetrievalService,
)
from app.domain.knowledge.models import KnowledgeChunk, KnowledgeQuery
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
)
from app.domain.knowledge.evidence import (
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceDecision,
    EvidenceSufficiency,
)
from app.domain.knowledge.engine import (
    KnowledgeEngine,
    RuntimeEngineExecution,
    RuntimeFallbackReason,
)
from app.services.knowledge_grounding import retrieve_grounding


def _result(engine: str, chunk_id: str) -> RetrievalResult:
    chunk = KnowledgeChunk(
        chunk_id=chunk_id,
        title=chunk_id,
        content=f"private body {chunk_id}",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={
            "content_sha256": "a" * 64,
            "corpus_manifest_sha256": "b" * 64,
        },
        score=0.9,
    )
    decision = EvidenceDecision(
        availability=EvidenceAvailability.AVAILABLE,
        sufficiency=EvidenceSufficiency.NOT_EVALUATED,
        evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
        reason_codes=(f"gate-{chunk_id}",),
        gate_version="retrieval-gate-v1",
    )
    return RetrievalResult(
        request_id="request-runtime",
        availability=RetrievalAvailability.AVAILABLE,
        candidates=[RetrievalCandidate(chunk=chunk, rerank_rank=1)],
        selected_evidence=[chunk],
        evidence_decision=decision,
        trace=RetrievalTrace(
            request_id="request-runtime",
            profile_id="profile",
            profile_version="v1",
            latency_ms=1,
        ),
        retrieval_engine_version=engine,
        profile_version="v1",
        latency_ms=1,
    )


def _unavailable_result(engine: str) -> RetrievalResult:
    result = _result(engine, "unavailable")
    return result.model_copy(
        update={
            "availability": RetrievalAvailability.UNAVAILABLE,
            "candidates": [],
            "selected_evidence": [],
            "evidence_decision": EvidenceDecision(
                availability=EvidenceAvailability.UNAVAILABLE,
                sufficiency=EvidenceSufficiency.NOT_EVALUATED,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("retrieval_unavailable",),
                gate_version="retrieval-gate-v1",
            ),
        }
    )


def _no_evidence_result(engine: str) -> RetrievalResult:
    result = _result(engine, "no-evidence")
    return result.model_copy(
        update={
            "candidates": [],
            "selected_evidence": [],
            "evidence_decision": EvidenceDecision(
                availability=EvidenceAvailability.AVAILABLE,
                sufficiency=EvidenceSufficiency.EMPTY,
                evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
                reason_codes=("no_relevant_candidate",),
                gate_version="retrieval-gate-v1",
            ),
        }
    )


class Engine:
    def __init__(self, result=None, *, fail=False):
        self.result = result
        self.fail = fail
        self.calls = []

    def retrieve(self, request, profile):
        self.calls.append((request, profile))
        if self.fail:
            raise RuntimeError("private provider detail")
        return self.result


class Sink:
    def __init__(self):
        self.traces = []

    def record_retrieval_trace(self, trace):
        self.traces.append(trace)


PROFILE = ResolvedRetrievalProfile(profile_id="profile", profile_version="v1")


def _request(scope="prep-runtime"):
    return RetrievalRequest(
        request_id="request-runtime",
        query_text="PRIVATE QUERY TEXT",
        intent=RetrievalIntent.PREP,
        profile_id="profile",
        prep_run_id=scope,
    )


def test_configured_engine_chooses_runtime_engine():
    legacy = Engine(_result("compatibility-v1", "legacy"))
    hybrid = Engine(_result("hybrid-v2", "hybrid"))

    legacy_outcome = RuntimeKnowledgeRetrievalService(
        legacy, hybrid, configured_engine="legacy"
    ).retrieve(_request("prep-zero"), legacy_profile=PROFILE, candidate_profile=PROFILE)
    hybrid_outcome = RuntimeKnowledgeRetrievalService(
        legacy, hybrid, configured_engine="hybrid-v2"
    ).retrieve(_request("prep-full"), legacy_profile=PROFILE, candidate_profile=PROFILE)

    assert legacy_outcome.execution.effective_engine == KnowledgeEngine.LEGACY
    assert legacy_outcome.result.retrieval_engine_version == "compatibility-v1"
    assert hybrid_outcome.execution.effective_engine == KnowledgeEngine.HYBRID_V2
    assert hybrid_outcome.result.retrieval_engine_version == "hybrid-v2"


def test_unavailable_hybrid_falls_back_with_a_stable_reason():
    legacy = Engine(_result("compatibility-v1", "legacy"))
    hybrid = Engine(_unavailable_result("hybrid-v2"))
    outcome = RuntimeKnowledgeRetrievalService(
        legacy,
        hybrid,
        configured_engine="hybrid-v2",
    ).retrieve(
        _request("prep-existing"),
        legacy_profile=PROFILE,
        candidate_profile=PROFILE,
    )

    assert outcome.execution.requested_engine == KnowledgeEngine.HYBRID_V2
    assert outcome.execution.effective_engine == KnowledgeEngine.LEGACY
    assert outcome.execution.fallback_reason == RuntimeFallbackReason.RETRIEVAL_UNAVAILABLE
    assert outcome.result.retrieval_engine_version == "compatibility-v1"
    assert len(hybrid.calls) == 1
    assert len(legacy.calls) == 1


def test_candidate_failure_runs_legacy_fallback_once():
    legacy = Engine(_result("compatibility-v1", "legacy"))
    hybrid = Engine(fail=True)

    outcome = RuntimeKnowledgeRetrievalService(
        legacy,
        hybrid,
        configured_engine="hybrid-v2",
    ).retrieve(
        _request("prep-candidate-failure"),
        legacy_profile=PROFILE,
        candidate_profile=PROFILE,
    )

    assert outcome.execution.fallback_reason == RuntimeFallbackReason.CANDIDATE_ENGINE_FAILED
    assert len(hybrid.calls) == 1
    assert len(legacy.calls) == 1


@pytest.mark.parametrize("candidate_result", [None, _unavailable_result("hybrid-v2")])
def test_legacy_fallback_failure_propagates_without_retry(candidate_result):
    legacy = Engine(fail=True)
    hybrid = Engine(candidate_result, fail=candidate_result is None)
    service = RuntimeKnowledgeRetrievalService(
        legacy,
        hybrid,
        configured_engine="hybrid-v2",
    )

    with pytest.raises(RuntimeError, match="private provider detail"):
        service.retrieve(
            _request("prep-fallback-failure"),
            legacy_profile=PROFILE,
            candidate_profile=PROFILE,
        )

    assert len(hybrid.calls) == 1
    assert len(legacy.calls) == 1


def test_no_evidence_is_not_treated_as_a_candidate_failure():
    legacy = Engine(_result("compatibility-v1", "legacy"))
    outcome = RuntimeKnowledgeRetrievalService(
        legacy,
        Engine(_no_evidence_result("hybrid-v2")),
        configured_engine="hybrid-v2",
    ).retrieve(
        _request("prep-no-evidence"),
        legacy_profile=PROFILE,
        candidate_profile=PROFILE,
    )

    assert outcome.execution.effective_engine == KnowledgeEngine.HYBRID_V2
    assert outcome.execution.fallback_reason is None
    assert outcome.result.evidence_decision.sufficiency == EvidenceSufficiency.EMPTY
    assert legacy.calls == []


def test_runtime_trace_records_engine_execution_without_private_query_or_content():
    sink = Sink()
    outcome = RuntimeKnowledgeRetrievalService(
        Engine(_result("compatibility-v1", "legacy")),
        Engine(_result("hybrid-v2", "hybrid")),
        configured_engine="hybrid-v2",
        trace_sink=sink,
    ).retrieve(_request(), legacy_profile=PROFILE, candidate_profile=PROFILE)

    assert outcome.execution.effective_engine == KnowledgeEngine.HYBRID_V2
    assert sink.traces[0]["execution"]["effective_engine"] == "hybrid-v2"
    assert sink.traces[0]["retrieval_trace"]["trace_schema_version"] == (
        "retrieval-trace-v3"
    )
    serialized = str(sink.traces)
    assert "PRIVATE QUERY TEXT" not in serialized
    assert "private body" not in serialized
    assert "shadow" not in serialized


def test_prep_grounding_uses_prep_intent_and_records_execution_per_query():
    execution = RuntimeEngineExecution(
        requested_engine=KnowledgeEngine.HYBRID_V2,
        effective_engine=KnowledgeEngine.HYBRID_V2,
        retrieval_availability="available",
        engine_version="hybrid-v2",
    )

    class RuntimeRepository:
        def __init__(self):
            self.calls = []

        def search_runtime(self, query_text, **kwargs):
            self.calls.append((query_text, kwargs))
            return SimpleNamespace(
                execution=execution,
                result=_result("hybrid-v2", f"chunk-{len(self.calls)}"),
            )

    queries = [
        KnowledgeQuery(
            query_id=f"query-{index}",
            topic_id=f"topic-{index}",
            query_text=f"query {index}",
            canonical_tag="redis",
        )
        for index in (1, 2)
    ]
    repository = RuntimeRepository()
    result = retrieve_grounding(
        queries,
        repository,
        prep_run_id="prep-grounding",
    )

    assert result.knowledge_engine_execution is execution
    assert all(call[1]["intent"] == RetrievalIntent.PREP for call in repository.calls)
    assert all("existing_assignment" not in call[1] for call in repository.calls)
    assert all(
        retrieval.engine_execution is execution for retrieval in result.retrievals
    )
