from __future__ import annotations

from types import SimpleNamespace

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
from app.domain.knowledge.rollout import KnowledgeEngine, assign_knowledge_engine
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


def test_zero_and_full_rollout_choose_formal_engine():
    legacy = Engine(_result("compatibility-v1", "legacy"))
    hybrid = Engine(_result("hybrid-v2", "hybrid"))

    zero = RuntimeKnowledgeRetrievalService(
        legacy, hybrid, rollout_percent=0, assignment_version="v1"
    ).retrieve(_request("prep-zero"), legacy_profile=PROFILE, candidate_profile=PROFILE)
    full = RuntimeKnowledgeRetrievalService(
        legacy, hybrid, rollout_percent=100, assignment_version="v1"
    ).retrieve(_request("prep-full"), legacy_profile=PROFILE, candidate_profile=PROFILE)

    assert zero.assignment.engine == KnowledgeEngine.LEGACY
    assert zero.result.retrieval_engine_version == "compatibility-v1"
    assert full.assignment.engine == KnowledgeEngine.HYBRID_V2
    assert full.result.retrieval_engine_version == "hybrid-v2"


def test_existing_assignment_is_reused_after_rollout_changes():
    existing = assign_knowledge_engine(
        "prep-existing", rollout_percent=100, assignment_version="old"
    )
    outcome = RuntimeKnowledgeRetrievalService(
        Engine(_result("compatibility-v1", "legacy")),
        Engine(_result("hybrid-v2", "hybrid")),
        rollout_percent=0,
        assignment_version="new",
    ).retrieve(
        _request("prep-existing"),
        legacy_profile=PROFILE,
        candidate_profile=PROFILE,
        existing_assignment=existing,
    )

    assert outcome.assignment is existing
    assert outcome.result.retrieval_engine_version == "hybrid-v2"


def test_shadow_returns_legacy_and_records_only_sanitized_comparison():
    sink = Sink()
    outcome = RuntimeKnowledgeRetrievalService(
        Engine(_result("compatibility-v1", "legacy")),
        Engine(_result("hybrid-v2", "hybrid")),
        rollout_percent=100,
        assignment_version="v1",
        shadow_enabled=True,
        trace_sink=sink,
    ).retrieve(_request(), legacy_profile=PROFILE, candidate_profile=PROFILE)

    assert outcome.result.retrieval_engine_version == "compatibility-v1"
    assert outcome.assignment.engine == KnowledgeEngine.LEGACY
    assert outcome.shadow_observation.selected_evidence_changed is True
    serialized = str(sink.traces)
    assert "PRIVATE QUERY TEXT" not in serialized
    assert "private body" not in serialized
    assert sink.traces[0]["retrieval_trace"]["trace_schema_version"] == (
        "retrieval-trace-v2"
    )
    assert outcome.shadow_observation.shadow_overhead_latency_ms >= 0
    assert outcome.shadow_observation.gate_changed is True
    assert outcome.shadow_observation.legacy.gate_reason_codes == ("gate-legacy",)
    assert outcome.shadow_observation.candidate.gate_reason_codes == ("gate-hybrid",)


def test_shadow_candidate_failure_isolated_from_formal_result():
    outcome = RuntimeKnowledgeRetrievalService(
        Engine(_result("compatibility-v1", "legacy")),
        Engine(fail=True),
        rollout_percent=100,
        assignment_version="v1",
        shadow_enabled=True,
    ).retrieve(_request(), legacy_profile=PROFILE, candidate_profile=PROFILE)

    assert outcome.result.retrieval_engine_version == "compatibility-v1"
    assert outcome.shadow_observation.reason_code == "shadow_candidate_failed"
    assert "private provider detail" not in outcome.shadow_observation.model_dump_json()


def test_prep_grounding_uses_prep_intent_and_reuses_assignment_across_queries():
    assignment = assign_knowledge_engine(
        "prep-grounding", rollout_percent=100, assignment_version="v1"
    )

    class RuntimeRepository:
        def __init__(self):
            self.calls = []

        def search_runtime(self, query_text, **kwargs):
            self.calls.append((query_text, kwargs))
            return SimpleNamespace(
                assignment=assignment,
                result=_result("hybrid-v2", f"chunk-{len(self.calls)}"),
                runtime_reason_code=None,
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

    assert result.knowledge_engine_assignment is assignment
    assert all(call[1]["intent"] == RetrievalIntent.PREP for call in repository.calls)
    assert repository.calls[0][1]["existing_assignment"] is None
    assert repository.calls[1][1]["existing_assignment"] is assignment
