from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.adapters.knowledge.source_aware_retriever import (
    SourceAwareKnowledgeRetriever,
)
from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
)
from app.application.knowledge.hybrid_retrieval_service import (
    HybridKnowledgeRetrievalService,
)
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelResult,
    RetrievalChannelTrace,
    RetrievalIntent,
    RetrievalRequest,
)
from app.domain.knowledge.source_scope import (
    SelectedUserDocumentRevision,
    build_knowledge_source_scope,
)
from app.domain.knowledge.user_document import UserDocumentChunk
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
)


NOW = datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc)
OWNER = "principal-a"


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return [1.0, 0.0]


class SystemChannel:
    def __init__(self, channel: str, candidates=()):
        self.channel = channel
        self.candidates = list(candidates)
        self.calls = []

    def retrieve_semantic(self, request, *, candidate_limit):
        assert self.channel == "semantic"
        return self._retrieve(request, candidate_limit)

    def retrieve_lexical(self, request, *, candidate_limit):
        assert self.channel == "lexical"
        return self._retrieve(request, candidate_limit)

    def _retrieve(self, request, candidate_limit):
        self.calls.append((request, candidate_limit))
        candidates = self.candidates[:candidate_limit]
        return RetrievalChannelResult(
            availability=RetrievalAvailability.AVAILABLE,
            candidates=candidates,
            trace=RetrievalChannelTrace(
                channel=self.channel,
                status="completed" if candidates else "empty",
                latency_ms=0,
                candidate_count=len(candidates),
                hit_ids=[item.chunk_id for item in candidates],
            ),
            corpus_version=("system-v1" if self.channel == "semantic" else None),
            corpus_manifest_sha256=("f" * 64 if self.channel == "semantic" else None),
        )


class DegradedSystemChannel(SystemChannel):
    def __init__(self, channel: str, candidates=(), *, reason_code: str):
        super().__init__(channel, candidates)
        self.reason_code = reason_code

    def _retrieve(self, request, candidate_limit):
        result = super()._retrieve(request, candidate_limit)
        return result.model_copy(
            update={
                "availability": RetrievalAvailability.DEGRADED,
                "trace": result.trace.model_copy(
                    update={"reason_code": self.reason_code}
                ),
            }
        )


class RecordingUserChunks:
    def __init__(self, inner):
        self.inner = inner
        self.semantic_calls = []
        self.lexical_calls = []

    def search_semantic(self, **kwargs):
        self.semantic_calls.append(kwargs)
        return self.inner.search_semantic(**kwargs)

    def search_lexical(self, **kwargs):
        self.lexical_calls.append(kwargs)
        return self.inner.search_lexical(**kwargs)


class UnavailableUserChunks:
    def search_semantic(self, **_kwargs):
        raise RuntimeError("private repository detail must not escape")

    def search_lexical(self, **_kwargs):
        raise RuntimeError("private repository detail must not escape")


class RecordingFactory:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


def _system_candidate(chunk_id: str, *, channel: str, score: float):
    chunk = KnowledgeChunk(
        chunk_id=chunk_id,
        title=f"System {chunk_id}",
        content="System Redis guidance",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={
            "content_sha256": "e" * 64,
            "corpus_manifest_sha256": "f" * 64,
        },
        score=score,
    )
    return RetrievalCandidate(
        chunk=chunk,
        semantic_score=score if channel == "semantic" else None,
        lexical_score=score if channel == "lexical" else None,
        semantic_rank=1 if channel == "semantic" else None,
        lexical_rank=1 if channel == "lexical" else None,
        channel_hits=[channel],
    )


def _selected(
    document_id: str,
    revision_id: str,
    *,
    allowed_usages=("question", "follow_up", "feedback"),
):
    return SelectedUserDocumentRevision(
        document_id=document_id,
        document_revision_id=revision_id,
        content_sha256="d" * 64,
        allowed_usages=allowed_usages,
    )


def _source_scope(
    *,
    include_system: bool,
    selected=(),
    usage="question",
):
    snapshot = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=include_system,
        selected_documents=tuple(selected),
        created_at=NOW,
    )
    return build_knowledge_source_scope(
        snapshot,
        owner_principal_id=OWNER if selected else None,
        usage=usage,
    )


def _request(source_scope, query="redis cache consistency"):
    return RetrievalRequest(
        query_text=query,
        intent=RetrievalIntent.PREP,
        profile_id="source-aware-test",
        source_scope=source_scope,
    )


def _user_chunk(
    document_id: str,
    revision_id: str,
    *,
    content="redis cache consistency",
    embedding=(1.0, 0.0),
):
    return UserDocumentChunk(
        chunk_id=str(uuid4()),
        owner_principal_id=OWNER,
        document_id=document_id,
        document_revision_id=revision_id,
        position=1,
        title="Private Redis notes",
        content=content,
        content_sha256="c" * 64,
        embedding=embedding,
        embedding_identity="fake:test:v1:2",
        created_at=NOW,
    )


def _user_repository(*chunks):
    repository = InMemoryUserDocumentChunkRepository()
    for chunk in chunks:
        repository.replace_revision_chunks(
            owner_principal_id=OWNER,
            document_id=chunk.document_id,
            document_revision_id=chunk.document_revision_id,
            chunks=(chunk,),
        )
    return RecordingUserChunks(repository)


def _adapter(*, semantic=(), lexical=(), user_repository=None):
    semantic_channel = SystemChannel("semantic", semantic)
    lexical_channel = SystemChannel("lexical", lexical)
    embeddings = FakeEmbeddingProvider()
    factory = RecordingFactory(user_repository or _user_repository())
    adapter = SourceAwareKnowledgeRetriever(
        semantic_channel,
        lexical_channel,
        user_chunks_factory=factory,
        embedding_provider=embeddings,
    )
    return adapter, semantic_channel, lexical_channel, factory, embeddings


def test_system_only_scope_preserves_existing_channel_behavior():
    expected = _system_candidate("system-semantic", channel="semantic", score=0.8)
    adapter, semantic, _, factory, embeddings = _adapter(semantic=(expected,))

    result = adapter.retrieve_semantic(
        _request(_source_scope(include_system=True)),
        candidate_limit=3,
    )

    assert result.candidates == [expected]
    assert len(semantic.calls) == 1
    assert factory.calls == 0
    assert embeddings.calls == []


def test_all_sources_disabled_returns_explicit_empty_without_any_provider_call():
    adapter, semantic, lexical, factory, embeddings = _adapter(
        semantic=(
            _system_candidate("system", channel="semantic", score=0.9),
        ),
        lexical=(
            _system_candidate("system", channel="lexical", score=0.9),
        ),
    )
    request = _request(_source_scope(include_system=False))

    semantic_result = adapter.retrieve_semantic(request, candidate_limit=2)
    lexical_result = adapter.retrieve_lexical(request, candidate_limit=2)

    assert semantic_result.availability == RetrievalAvailability.AVAILABLE
    assert lexical_result.availability == RetrievalAvailability.AVAILABLE
    assert semantic_result.candidates == lexical_result.candidates == []
    assert semantic.calls == lexical.calls == []
    assert factory.calls == 0
    assert embeddings.calls == []


def test_user_only_scope_queries_only_frozen_revision_and_maps_safe_metadata():
    document_a, revision_a = str(uuid4()), str(uuid4())
    document_b, revision_b = str(uuid4()), str(uuid4())
    chunk_a = _user_chunk(document_a, revision_a)
    chunk_b = _user_chunk(document_b, revision_b)
    users = _user_repository(chunk_a, chunk_b)
    adapter, semantic, _, _, _ = _adapter(user_repository=users)
    scope = _source_scope(
        include_system=False,
        selected=(_selected(document_a, revision_a),),
    )

    result = adapter.retrieve_semantic(_request(scope), candidate_limit=5)

    assert semantic.calls == []
    assert [item.chunk_id for item in result.candidates] == [chunk_a.chunk_id]
    assert users.semantic_calls[0]["allowed_document_revision_ids"] == (
        revision_a,
    )
    metadata = result.candidates[0].chunk.metadata
    assert metadata["knowledge_source"] == "user_material"
    assert metadata["document_id"] == document_a
    assert metadata["document_revision_id"] == revision_a
    assert metadata["content_sha256"] == chunk_a.content_sha256
    assert metadata["document_content_sha256"] == "d" * 64
    assert metadata["provenance"] == {
        "knowledge_source": "user_material",
        "document_id": document_a,
        "document_revision_id": revision_a,
        "document_content_sha256": "d" * 64,
    }
    assert "owner" not in metadata
    assert "owner_principal_id" not in metadata
    assert "path" not in metadata
    assert "owner_principal_id" not in metadata["provenance"]
    assert "original_filename" not in metadata["provenance"]


def test_usage_filter_removes_disallowed_user_revision_before_repository_access():
    document_id, revision_id = str(uuid4()), str(uuid4())
    users = _user_repository(_user_chunk(document_id, revision_id))
    adapter, semantic, lexical, factory, embeddings = _adapter(
        user_repository=users
    )
    scope = _source_scope(
        include_system=False,
        selected=(
            _selected(
                document_id,
                revision_id,
                allowed_usages=("question",),
            ),
        ),
        usage="feedback",
    )
    request = _request(scope)

    assert adapter.retrieve_semantic(request, candidate_limit=3).candidates == []
    assert adapter.retrieve_lexical(request, candidate_limit=3).candidates == []
    assert scope.selected_documents == ()
    assert semantic.calls == lexical.calls == []
    assert factory.calls == 0
    assert embeddings.calls == []


def test_system_and_user_share_one_semantic_rank_and_one_global_limit():
    document_id, revision_id = str(uuid4()), str(uuid4())
    user = _user_chunk(document_id, revision_id, embedding=(1.0, 0.0))
    users = _user_repository(user)
    system_high = _system_candidate(
        "system-high",
        channel="semantic",
        score=0.9,
    )
    system_low = _system_candidate(
        "system-low",
        channel="semantic",
        score=0.8,
    )
    adapter, semantic, _, _, _ = _adapter(
        semantic=(system_high, system_low),
        user_repository=users,
    )
    scope = _source_scope(
        include_system=True,
        selected=(_selected(document_id, revision_id),),
    )

    result = adapter.retrieve_semantic(_request(scope), candidate_limit=2)

    assert [item.chunk_id for item in result.candidates] == [
        user.chunk_id,
        "system-high",
    ]
    assert [item.semantic_rank for item in result.candidates] == [1, 2]
    assert len(result.candidates) == 2
    assert semantic.calls[0][1] == 2
    assert users.semantic_calls[0]["limit"] == 2
    assert result.trace.channel == "semantic"


def test_system_and_user_share_one_lexical_rank_and_one_global_limit():
    document_id, revision_id = str(uuid4()), str(uuid4())
    user = _user_chunk(
        document_id,
        revision_id,
        content="redis redis redis consistency",
    )
    users = _user_repository(user)
    system = _system_candidate("system-lexical", channel="lexical", score=0.01)
    adapter, _, lexical, _, _ = _adapter(
        lexical=(system,),
        user_repository=users,
    )
    scope = _source_scope(
        include_system=True,
        selected=(_selected(document_id, revision_id),),
    )

    result = adapter.retrieve_lexical(
        _request(scope, query="redis consistency"),
        candidate_limit=1,
    )

    assert [item.chunk_id for item in result.candidates] == [user.chunk_id]
    assert result.candidates[0].lexical_rank == 1
    assert lexical.calls[0][1] == 1
    assert users.lexical_calls[0]["limit"] == 1
    assert result.trace.channel == "lexical"


@pytest.mark.parametrize(
    ("channel", "reason_code"),
    (
        ("semantic", "semantic_timeout"),
        ("lexical", "lexical_timeout"),
    ),
)
def test_degraded_system_channel_stays_degraded_when_user_channel_is_available(
    channel,
    reason_code,
):
    document_id, revision_id = str(uuid4()), str(uuid4())
    excluded_document_id, excluded_revision_id = str(uuid4()), str(uuid4())
    selected_user = _user_chunk(document_id, revision_id)
    excluded_user = _user_chunk(excluded_document_id, excluded_revision_id)
    users = _user_repository(selected_user, excluded_user)
    system_candidate = _system_candidate(
        f"system-{channel}",
        channel=channel,
        score=0.9,
    )
    semantic = (
        DegradedSystemChannel(
            "semantic",
            (system_candidate,),
            reason_code=reason_code,
        )
        if channel == "semantic"
        else SystemChannel("semantic")
    )
    lexical = (
        DegradedSystemChannel(
            "lexical",
            (system_candidate,),
            reason_code=reason_code,
        )
        if channel == "lexical"
        else SystemChannel("lexical")
    )
    adapter = SourceAwareKnowledgeRetriever(
        semantic,
        lexical,
        user_chunks_factory=RecordingFactory(users),
        embedding_provider=FakeEmbeddingProvider(),
    )
    scope = _source_scope(
        include_system=True,
        selected=(_selected(document_id, revision_id),),
    )
    private_query = "PRIVATE QUERY redis cache consistency"

    result = getattr(adapter, f"retrieve_{channel}")(
        _request(scope, query=private_query),
        candidate_limit=1,
    )

    assert result.availability == RetrievalAvailability.DEGRADED
    assert result.trace.reason_code == reason_code
    assert len(result.candidates) == 1
    assert excluded_user.chunk_id not in {
        candidate.chunk_id for candidate in result.candidates
    }
    repository_call = (
        users.semantic_calls[0]
        if channel == "semantic"
        else users.lexical_calls[0]
    )
    assert repository_call["allowed_document_revision_ids"] == (revision_id,)
    serialized_trace = json.dumps(result.trace.model_dump(mode="json"))
    for private_value in (
        private_query,
        OWNER,
        document_id,
        revision_id,
        excluded_document_id,
        excluded_revision_id,
        "repository raw exception",
    ):
        assert private_value not in serialized_trace


def test_first_non_available_reason_wins_without_leaking_later_repository_failure():
    document_id, revision_id = str(uuid4()), str(uuid4())
    system_candidate = _system_candidate(
        "system-degraded-safe",
        channel="semantic",
        score=0.9,
    )
    adapter = SourceAwareKnowledgeRetriever(
        DegradedSystemChannel(
            "semantic",
            (system_candidate,),
            reason_code="semantic_timeout",
        ),
        SystemChannel("lexical"),
        user_chunks_factory=RecordingFactory(UnavailableUserChunks()),
        embedding_provider=FakeEmbeddingProvider(),
    )
    scope = _source_scope(
        include_system=True,
        selected=(_selected(document_id, revision_id),),
    )

    result = adapter.retrieve_semantic(
        _request(scope, query="PRIVATE FAILURE QUERY"),
        candidate_limit=1,
    )

    assert result.availability == RetrievalAvailability.DEGRADED
    assert result.trace.reason_code == "semantic_timeout"
    assert [item.chunk_id for item in result.candidates] == [
        system_candidate.chunk_id
    ]
    serialized_trace = json.dumps(result.trace.model_dump(mode="json"))
    assert "PRIVATE FAILURE QUERY" not in serialized_trace
    assert OWNER not in serialized_trace
    assert document_id not in serialized_trace
    assert revision_id not in serialized_trace
    assert "private repository detail" not in serialized_trace


def test_hybrid_trace_contains_only_safe_scope_summary_and_hash():
    document_id, revision_id = str(uuid4()), str(uuid4())
    private_body = "PRIVATE USER MATERIAL BODY redis consistency"
    user = _user_chunk(
        document_id,
        revision_id,
        content=private_body,
    )
    users = _user_repository(user)
    adapter, _, _, _, _ = _adapter(user_repository=users)
    scope = _source_scope(
        include_system=False,
        selected=(_selected(document_id, revision_id),),
    )
    profile = ResolvedRetrievalProfile(
        profile_id="source-aware-test",
        profile_version="v1",
        semantic_enabled=True,
        lexical_enabled=True,
        semantic_candidate_limit=3,
        lexical_candidate_limit=3,
        fusion_candidate_limit=3,
        rerank_candidate_limit=3,
        evidence_limit=2,
        minimum_score=0,
    )

    result = HybridKnowledgeRetrievalService(adapter, adapter).retrieve(
        _request(scope, query="PRIVATE QUERY redis consistency"),
        profile,
    )
    trace = result.trace.model_dump(mode="json")
    serialized = json.dumps(trace, sort_keys=True)

    assert trace["source_scope"] == {
        "include_system_knowledge": False,
        "user_document_count": 1,
        "usage": "question",
        "source_scope_sha256": scope.source_scope_sha256,
    }
    assert [chunk.chunk_id for chunk in result.selected_evidence] == [
        user.chunk_id
    ]
    assert "invalid_knowledge_metadata" not in (
        result.evidence_decision.reason_codes
    )
    assert OWNER not in serialized
    assert document_id not in serialized
    assert revision_id not in serialized
    assert private_body not in serialized
    assert "PRIVATE QUERY" not in serialized
    assert "selected_documents" not in serialized


def test_user_repository_failure_is_degraded_without_widening_or_leaking():
    document_id, revision_id = str(uuid4()), str(uuid4())
    system_semantic = _system_candidate(
        "system-safe",
        channel="semantic",
        score=0.9,
    )
    system_lexical = _system_candidate(
        "system-safe",
        channel="lexical",
        score=0.9,
    )
    adapter, _, _, _, _ = _adapter(
        semantic=(system_semantic,),
        lexical=(system_lexical,),
        user_repository=UnavailableUserChunks(),
    )
    scope = _source_scope(
        include_system=True,
        selected=(_selected(document_id, revision_id),),
    )
    profile = ResolvedRetrievalProfile(
        profile_id="source-aware-test",
        profile_version="v1",
        semantic_enabled=True,
        lexical_enabled=True,
        semantic_candidate_limit=3,
        lexical_candidate_limit=3,
        fusion_candidate_limit=3,
        rerank_candidate_limit=3,
        evidence_limit=2,
        minimum_score=0,
    )
    service = HybridKnowledgeRetrievalService(adapter, adapter)

    try:
        result = service.retrieve(
            _request(scope, query="PRIVATE FAILURE QUERY"),
            profile,
        )
    finally:
        service.close()

    assert result.availability == RetrievalAvailability.DEGRADED
    assert [chunk.chunk_id for chunk in result.selected_evidence] == [
        "system-safe"
    ]
    assert set(result.degraded_reasons) == {
        "user_material_semantic_unavailable",
        "user_material_lexical_unavailable",
    }
    serialized = json.dumps(result.trace.model_dump(mode="json"), sort_keys=True)
    assert OWNER not in serialized
    assert document_id not in serialized
    assert revision_id not in serialized
    assert "PRIVATE FAILURE QUERY" not in serialized
    assert "private repository detail" not in serialized
