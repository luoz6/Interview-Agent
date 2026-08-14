from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.api.rag.models import (
    ConsumerActionRecord,
    CorpusReleaseRequest,
    CorpusValidateRequest,
    RetrievalInspectionRequest,
    RetrievalCompareRequest,
    SafeRetrievalCompareResponse,
    SafeRetrievalCandidate,
)
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelTrace,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
    build_retrieval_trace,
    RetrievalRerankSummary,
)
from app.domain.knowledge.evidence_gate import RetrievalEvidenceGate
from app.services.knowledge_eval_artifacts_v3 import (
    build_retrieval_diagnostic_snapshot_v1,
)
from app.application.knowledge.diagnostic_models import EvidenceTraceResponse
from app.application.knowledge.diagnostics_service import safe_diagnostic_excerpt


def test_console_models_forbid_unknown_fields_and_blank_queries():
    try:
        RetrievalInspectionRequest(query_text="query", unexpected=True)
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown request fields must be rejected")

    try:
        RetrievalInspectionRequest(query_text="   ")
    except ValidationError:
        pass
    else:
        raise AssertionError("blank query must be rejected")

    for payload in (
        {"query_text": "query", "engine": "legacy"},
        {"query_text": "   "},
    ):
        with pytest.raises(ValidationError):
            RetrievalCompareRequest.model_validate(payload)


def test_compare_contract_never_exposes_query_or_provider_payload_fields():
    request_fields = set(RetrievalCompareRequest.model_fields)
    assert "engine" not in request_fields
    response_fields = set(SafeRetrievalCompareResponse.model_fields)
    assert not response_fields.intersection(
        {"query_text", "provider_payload", "resume", "job_description"}
    )


def test_corpus_write_contracts_do_not_put_source_content_in_response_models():
    request_fields = set(CorpusValidateRequest.model_fields)
    release_fields = set(CorpusReleaseRequest.model_fields)
    assert request_fields == {"entry"}
    assert "entry" in release_fields

    from app.application.knowledge.diagnostic_models import (
        CorpusReleaseResponse,
        CorpusValidateResponse,
    )

    forbidden = {"content", "entry", "references", "source_url", "provider_payload"}
    assert not set(CorpusValidateResponse.model_fields).intersection(forbidden)
    assert not set(CorpusReleaseResponse.model_fields).intersection(forbidden)


def test_safe_candidate_has_no_raw_content_metadata_query_or_url_fields():
    fields = set(SafeRetrievalCandidate.model_fields)
    assert not fields.intersection(
        {"content", "metadata", "query_text", "source_url", "embedding"}
    )


def test_all_diagnostic_contracts_forbid_unknown_fields():
    from app.application.knowledge import diagnostic_models

    contract_types = [
        value
        for value in vars(diagnostic_models).values()
        if isinstance(value, type)
        and issubclass(value, diagnostic_models.SafeModel)
        and value is not diagnostic_models.SafeModel
    ]
    assert contract_types
    assert all(model.model_config.get("extra") == "forbid" for model in contract_types)


def test_evidence_trace_contract_exposes_only_allowlisted_lineage_fields():
    fields = set(EvidenceTraceResponse.model_fields)
    assert not fields.intersection(
        {"query_text", "answer", "resume", "job_description", "provider_payload", "chain_of_thought"}
    )


def test_consumer_action_is_explicitly_not_recorded_by_default():
    action = ConsumerActionRecord()
    assert action.recording_status == "not_recorded"
    assert action.public_message == "Not recorded / no unified policy"


def test_safe_diagnostic_excerpt_normalizes_controls_and_enforces_length():
    private_body = "line one\x00\n\tline two " + ("x" * 400)

    excerpt = safe_diagnostic_excerpt(private_body)

    assert excerpt.startswith("line one line two ")
    assert len(excerpt) == 320
    assert not any(ord(character) < 32 for character in excerpt)
    assert safe_diagnostic_excerpt(None) == ""


def test_snapshot_is_self_hashed_and_never_contains_raw_query():
    chunk = KnowledgeChunk(
        chunk_id="redis-lock",
        title="Redis lock",
        content="Safe diagnostic source body",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={"content_sha256": "a" * 64},
        score=0.9,
    )
    request = RetrievalRequest(
        request_id="req-snapshot",
        query_text="private query text",
        intent=RetrievalIntent.EVAL,
        profile_id="eval",
    )
    profile = ResolvedRetrievalProfile(profile_id="eval", profile_version="v1")
    candidate = RetrievalCandidate(
        chunk=chunk,
        semantic_rank=1,
        semantic_score=0.9,
        rerank_rank=1,
        rerank_score=0.9,
        channel_hits=["semantic"],
    )
    decision = RetrievalEvidenceGate().decide_selection(
        RetrievalAvailability.AVAILABLE, [chunk]
    )
    trace = build_retrieval_trace(
        request=request,
        profile=profile,
        channels=[
            RetrievalChannelTrace(
                channel="semantic",
                status="completed",
                latency_ms=1,
                candidate_count=1,
                hit_ids=[chunk.chunk_id],
            )
        ],
        selected_evidence=[chunk],
        degraded_reasons=[],
        latency_ms=2,
        latency_breakdown_ms={"semantic": 1, "rerank": 0.5, "evidence_gate": 0.5, "total": 2},
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
        component_versions={"retrieval_engine_version": "test-v1"},
    )
    result = RetrievalResult(
        request_id=request.request_id,
        availability=RetrievalAvailability.AVAILABLE,
        candidates=[candidate],
        selected_evidence=[chunk],
        evidence_decision=decision,
        trace=trace,
        retrieval_engine_version="test-v1",
        profile_version="v1",
        latency_ms=2,
    )
    snapshot = build_retrieval_diagnostic_snapshot_v1(
        artifact_sha256="b" * 64,
        case_id="case-1",
        result=result,
        created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    rendered = snapshot.model_dump_json()
    assert "private query text" not in rendered
    assert "Safe diagnostic source body" not in rendered
    assert snapshot.query_sha256 == trace.sanitized_query_facts.query_sha256
    assert snapshot.trace_schema_version == "retrieval-trace-v3"
    assert len(snapshot.snapshot_sha256) == 64
