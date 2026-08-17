from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.adapters.knowledge.runtime_repository import RuntimeKnowledgeRepository
from app.domain.knowledge.engine import KnowledgeEngine, RuntimeEngineExecution
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalIntent,
)
from app.domain.knowledge.models import KnowledgeChunk, KnowledgeQuery
from app.domain.knowledge.source_scope import (
    SelectedUserDocumentRevision,
    build_knowledge_source_scope,
)
from app.runtime.config import load_knowledge_runtime_settings
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.knowledge_grounding import retrieve_grounding
from app.services.prep import prepare_interview
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.session_plan_binding import session_plan_binding_from_revision
from tests.unit.test_grounded_knowledge_agent import GroundedPlanLLM
from tests.unit.test_interview_plan_revision import plan, source


OWNER_A = "principal-a"
OWNER_B = "principal-b"


def _selected(*, allowed_usages):
    return SelectedUserDocumentRevision(
        document_id=str(uuid4()),
        document_revision_id=str(uuid4()),
        content_sha256="d" * 64,
        allowed_usages=allowed_usages,
    )


def _snapshot(*selected, include_system=True):
    from datetime import datetime, timezone

    return build_interview_knowledge_scope_snapshot(
        include_system_knowledge=include_system,
        selected_documents=tuple(selected),
        created_at=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
    )


def test_internal_source_scope_is_deterministic_usage_filtered_and_owner_private():
    question_only = _selected(allowed_usages=("question",))
    feedback = _selected(allowed_usages=("question", "feedback"))
    snapshot = _snapshot(question_only, feedback, include_system=False)

    owner_a = build_knowledge_source_scope(
        snapshot,
        owner_principal_id=OWNER_A,
        usage="feedback",
    )
    owner_b = build_knowledge_source_scope(
        snapshot,
        owner_principal_id=OWNER_B,
        usage="feedback",
    )

    assert owner_a.selected_documents == (feedback,)
    assert owner_a.allowed_document_revision_ids == (
        feedback.document_revision_id,
    )
    assert owner_a.owner_principal_id == OWNER_A
    assert owner_b.owner_principal_id == OWNER_B
    assert owner_a.source_scope_sha256 == owner_b.source_scope_sha256
    assert OWNER_A not in owner_a.source_scope_sha256
    assert OWNER_B not in owner_b.source_scope_sha256


class CapturingCoordinator:
    def __init__(self):
        self.requests = []

    def retrieve(self, request, **_kwargs):
        self.requests.append(request)
        return SimpleNamespace(
            result=SimpleNamespace(
                availability=RetrievalAvailability.AVAILABLE,
            )
        )

    def close(self):
        pass


class SessionStore:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def get(self, session_id):
        self.calls.append(session_id)
        return self.state


def test_session_runtime_retrieval_uses_authoritative_plan_snapshot_scope():
    feedback = _selected(allowed_usages=("feedback",))
    question_only = _selected(allowed_usages=("question",))
    frozen_snapshot = _snapshot(
        feedback,
        question_only,
        include_system=False,
    )
    revision_store = InMemoryInterviewPlanRevisionStore()
    revision = revision_store.create_initial(
        source_payload=source(),
        plan=plan().model_copy(
            update={"knowledge_scope": frozen_snapshot}
        ),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    binding = session_plan_binding_from_revision(revision)
    session_store = SessionStore(binding.model_dump(mode="json"))
    coordinator = CapturingCoordinator()
    repository = RuntimeKnowledgeRepository(
        SimpleNamespace(),
        coordinator,
        load_knowledge_runtime_settings(environ={}),
        session_store_factory=lambda: session_store,
        principal_identity_resolver_factory=lambda: (
            ExplicitPrincipalIdentityResolver(
                deployment_id="scope-contract",
                principal_id=OWNER_A,
            )
        ),
        materials_settings_factory=lambda: SimpleNamespace(enabled=True),
    )
    untrusted_override = build_knowledge_source_scope(
        _snapshot(include_system=True),
        owner_principal_id=None,
        usage="question",
    )

    repository.search_runtime(
        "review this answer",
        intent=RetrievalIntent.QUESTION_REVIEW,
        job_tags=["redis"],
        session_id="session-frozen-scope",
        source_scope=untrusted_override,
    )

    request_scope = coordinator.requests[0].source_scope
    assert session_store.calls == ["session-frozen-scope"]
    assert request_scope.include_system_knowledge is False
    assert request_scope.owner_principal_id == OWNER_A
    assert request_scope.usage == "feedback"
    assert request_scope.selected_documents == (feedback,)
    assert request_scope.allowed_document_revision_ids == (
        feedback.document_revision_id,
    )
    assert request_scope != untrusted_override


def test_disabled_materials_fail_closed_for_prep_user_scope_before_retrieval():
    selected = _selected(allowed_usages=("question",))
    source_scope = build_knowledge_source_scope(
        _snapshot(selected, include_system=True),
        owner_principal_id=OWNER_A,
        usage="question",
    )
    coordinator = CapturingCoordinator()
    repository = RuntimeKnowledgeRepository(
        SimpleNamespace(),
        coordinator,
        load_knowledge_runtime_settings(environ={}),
        materials_settings_factory=lambda: SimpleNamespace(enabled=False),
    )

    with pytest.raises(RuntimeError, match="user materials retrieval is disabled"):
        repository.search_runtime(
            "prep with selected notes",
            intent=RetrievalIntent.PREP,
            job_tags=["redis"],
            prep_run_id="prep-disabled-materials",
            source_scope=source_scope,
        )

    assert coordinator.requests == []


def test_disabled_materials_fail_closed_for_session_scope_before_retrieval():
    selected = _selected(allowed_usages=("feedback",))
    revision_store = InMemoryInterviewPlanRevisionStore()
    revision = revision_store.create_initial(
        source_payload=source(),
        plan=plan().model_copy(
            update={
                "knowledge_scope": _snapshot(selected, include_system=False)
            }
        ),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    session_store = SessionStore(
        session_plan_binding_from_revision(revision).model_dump(mode="json")
    )
    coordinator = CapturingCoordinator()

    def principal_factory():
        raise AssertionError("disabled materials must fail before Principal lookup")

    repository = RuntimeKnowledgeRepository(
        SimpleNamespace(),
        coordinator,
        load_knowledge_runtime_settings(environ={}),
        session_store_factory=lambda: session_store,
        principal_identity_resolver_factory=principal_factory,
        materials_settings_factory=lambda: SimpleNamespace(enabled=False),
    )

    with pytest.raises(RuntimeError, match="user materials retrieval is disabled"):
        repository.search_runtime(
            "session review with selected notes",
            intent=RetrievalIntent.QUESTION_REVIEW,
            job_tags=["redis"],
            session_id="session-disabled-materials",
        )

    assert session_store.calls == ["session-disabled-materials"]
    assert coordinator.requests == []


def test_disabled_materials_preserve_system_only_and_legacy_retrieval():
    coordinator = CapturingCoordinator()
    repository = RuntimeKnowledgeRepository(
        SimpleNamespace(),
        coordinator,
        load_knowledge_runtime_settings(environ={}),
        materials_settings_factory=lambda: SimpleNamespace(enabled=False),
    )
    system_scope = build_knowledge_source_scope(
        _snapshot(include_system=True),
        owner_principal_id=None,
        usage="question",
    )

    repository.search_runtime(
        "explicit system scope",
        intent=RetrievalIntent.PREP,
        job_tags=["redis"],
        source_scope=system_scope,
    )
    repository.search_runtime(
        "legacy system scope",
        intent=RetrievalIntent.PREP,
        job_tags=["redis"],
    )

    assert [request.source_scope for request in coordinator.requests] == [
        system_scope,
        None,
    ]


class CapturingGroundingRepository:
    def __init__(self, chunk):
        self.chunk = chunk
        self.calls = []
        self.execution = RuntimeEngineExecution(
            requested_engine=KnowledgeEngine.HYBRID_V2,
            effective_engine=KnowledgeEngine.HYBRID_V2,
            retrieval_availability="available",
            engine_version="fake-hybrid-v2",
        )

    def search_runtime(self, query_text, **kwargs):
        self.calls.append((query_text, kwargs))
        return SimpleNamespace(
            execution=self.execution,
            result=SimpleNamespace(
                selected_evidence=[self.chunk],
                degraded_reasons=[],
                availability=RetrievalAvailability.AVAILABLE,
                request_id=f"request-{len(self.calls)}",
                retrieval_engine_version="fake-hybrid-v2",
                profile_version="fake-v1",
                trace=SimpleNamespace(
                    resolved_profile=None,
                    component_versions=None,
                ),
            ),
        )


def test_prep_grounding_explicitly_forwards_resolved_source_scope():
    selected = _selected(
        allowed_usages=("question", "follow_up", "feedback")
    )
    snapshot = _snapshot(selected, include_system=False)
    source_scope = build_knowledge_source_scope(
        snapshot,
        owner_principal_id=OWNER_A,
        usage="question",
    )
    user_chunk = KnowledgeChunk(
        chunk_id="user-material-redis-consistency",
        title="Redis consistency notes",
        content="Use idempotency and repair checks around cache invalidation.",
        source_type="user_material",
        domain="redis",
        tags=["redis", "cache"],
        metadata={
            "knowledge_source": "user_material",
            "document_id": selected.document_id,
            "document_revision_id": selected.document_revision_id,
            "content_sha256": "c" * 64,
            "document_content_sha256": selected.content_sha256,
        },
        score=0.93,
    )
    repository = CapturingGroundingRepository(user_chunk)

    prepared = prepare_interview(
        "Backend engineer using Redis",
        "Built distributed cache systems",
        llm=GroundedPlanLLM(),
        knowledge_store=repository,
        knowledge_scope=snapshot,
        knowledge_source_scope=source_scope,
        allow_fallback=False,
    )

    assert prepared.title == "Provider grounded plan"
    assert prepared._revision_plan.knowledge_scope == snapshot
    assert [
        evidence.evidence_id for evidence in prepared.prep_context.evidence_refs
    ] == [user_chunk.chunk_id]
    assert (
        prepared.prep_context.evidence_refs[0].corpus_manifest_sha256 == ""
    )
    assert repository.calls
    assert all(
        kwargs["source_scope"] == source_scope
        for _, kwargs in repository.calls
    )


def test_explicit_scope_never_falls_back_to_an_unscoped_legacy_repository():
    selected = _selected(allowed_usages=("question",))
    source_scope = build_knowledge_source_scope(
        _snapshot(selected, include_system=False),
        owner_principal_id=OWNER_A,
        usage="question",
    )

    class LegacyOnlyRepository:
        def __init__(self):
            self.search_calls = 0

        def search(self, *_args, **_kwargs):
            self.search_calls += 1
            raise AssertionError("explicit scope must not widen to legacy search")

    legacy = LegacyOnlyRepository()
    result = retrieve_grounding(
        [
            KnowledgeQuery(
                query_id="query-explicit-scope",
                topic_id="topic-explicit-scope",
                query_text="redis consistency",
                canonical_tag="redis",
            )
        ],
        legacy,
        prep_run_id="prep-explicit-scope",
        source_scope=source_scope,
    )

    assert result.status == "degraded"
    assert result.degraded_reason == "knowledge_unavailable"
    assert legacy.search_calls == 0
