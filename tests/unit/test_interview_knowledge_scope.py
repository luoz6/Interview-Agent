from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.prep.routes as prep_route_module
import app.api.shared.dependencies as dependencies
from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
    InMemoryUserDocumentStore,
)
from app.api.shared.models import PrepKnowledgeScopeRequest
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.domain.knowledge.user_document import (
    UserDocumentInternalStage,
    UserDocumentPublicStatus,
)
from app.main import app
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.interview_knowledge_scope import (
    InterviewKnowledgeScopeError,
    InterviewKnowledgeScopeResolver,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.prep import bind_prepared_plan_revision, fallback_interview_plan
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from tests.vector_store_fixtures import FakeEmbeddingProvider


NOW = datetime(2026, 8, 15, 8, 30, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"


def _materials():
    store = InMemoryUserDocumentStore()
    chunks = InMemoryUserDocumentChunkRepository()
    ingestion = UserDocumentIngestionService(
        store=store,
        chunks=chunks,
        embedder=FakeEmbeddingProvider(),
        clock=lambda: NOW,
    )
    return store, ingestion


def _ingest(ingestion, *, owner=OWNER_A, name="scope.txt", content=b"scope"):
    return ingestion.ingest(
        owner_principal_id=owner,
        original_filename=name,
        media_type="text/plain",
        content=content,
    )


def _assert_scope_error(resolver, *, owner, document_id, code):
    with pytest.raises(InterviewKnowledgeScopeError) as exc_info:
        resolver.resolve(
            owner_principal_id=owner,
            selected_document_ids=(document_id,),
            include_system_knowledge=True,
        )
    assert exc_info.value.code == code


def _assert_scope_unavailable(resolver, *, owner, document_id):
    _assert_scope_error(
        resolver,
        owner=owner,
        document_id=document_id,
        code="knowledge_scope_document_unavailable",
    )


def test_prep_scope_request_is_frozen_and_preserves_input_for_resolver():
    first = "00000000-0000-0000-0000-000000000001"
    second_upper = "00000000-0000-0000-0000-0000000000AA"

    request = PrepKnowledgeScopeRequest(
        include_system_knowledge=False,
        selected_document_ids=[second_upper, first],
    )

    assert request.selected_document_ids == (
        second_upper,
        first,
    )
    with pytest.raises(ValidationError, match="frozen"):
        request.include_system_knowledge = True
    with pytest.raises(ValidationError):
        PrepKnowledgeScopeRequest(
            include_system_knowledge=True,
            selected_document_ids=[first],
            owner_principal_id=OWNER_A,
        )
    assert PrepKnowledgeScopeRequest(
        include_system_knowledge=True,
        selected_document_ids=["not-a-uuid"],
    ).selected_document_ids == ("not-a-uuid",)
    assert PrepKnowledgeScopeRequest(
        include_system_knowledge=True,
        selected_document_ids=[first, first],
    ).selected_document_ids == (first, first)
    assert PrepKnowledgeScopeRequest(
        include_system_knowledge=False,
        selected_document_ids=[],
    ).selected_document_ids == ()


def test_resolver_freezes_active_revision_and_canonical_selection_identity():
    store, ingestion = _materials()
    first = _ingest(ingestion, name="first.txt", content=b"first")
    second = _ingest(ingestion, name="second.txt", content=b"second")
    second = second.model_copy(update={"allowed_usages": ("feedback", "question")})
    store.save_document(owner_principal_id=OWNER_A, document=second)
    resolver = InterviewKnowledgeScopeResolver(store=store, clock=lambda: NOW)

    reverse = resolver.resolve(
        owner_principal_id=OWNER_A,
        selected_document_ids=(second.document_id, first.document_id),
        include_system_knowledge=False,
    )
    forward = resolver.resolve(
        owner_principal_id=OWNER_A,
        selected_document_ids=(first.document_id, second.document_id),
        include_system_knowledge=False,
    )

    assert reverse == forward
    assert reverse.selection_sha256 == forward.selection_sha256
    assert [item.document_id for item in reverse.selected_documents] == sorted(
        [first.document_id, second.document_id]
    )
    for selected in reverse.selected_documents:
        document = store.get_document(
            owner_principal_id=OWNER_A,
            document_id=selected.document_id,
        )
        revision = store.get_revision(
            owner_principal_id=OWNER_A,
            document_revision_id=document.active_revision_id,
        )
        assert selected.document_revision_id == revision.document_revision_id
        assert selected.content_sha256 == revision.content_sha256
        assert selected.allowed_usages == document.allowed_usages


def test_resolver_makes_missing_cross_owner_and_invalid_ids_non_enumerable():
    store, ingestion = _materials()
    document = _ingest(ingestion)
    resolver = InterviewKnowledgeScopeResolver(store=store, clock=lambda: NOW)

    _assert_scope_unavailable(
        resolver,
        owner=OWNER_B,
        document_id=document.document_id,
    )
    _assert_scope_unavailable(
        resolver,
        owner=OWNER_A,
        document_id=str(uuid4()),
    )
    _assert_scope_unavailable(
        resolver,
        owner=OWNER_A,
        document_id="not-a-uuid",
    )

    active_revision = store.get_revision(
        owner_principal_id=OWNER_A,
        document_revision_id=document.active_revision_id,
    )

    class RevisionMismatchStore:
        def get_document(self, **_kwargs):
            return document

        def get_revision(self, **_kwargs):
            return active_revision.model_copy(update={"document_id": str(uuid4())})

    _assert_scope_unavailable(
        InterviewKnowledgeScopeResolver(
            store=RevisionMismatchStore(),
            clock=lambda: NOW,
        ),
        owner=OWNER_A,
        document_id=document.document_id,
    )


@pytest.mark.parametrize(
    "updates",
    (
        {
            "public_status": UserDocumentPublicStatus.DISABLED,
            "enabled": False,
        },
        {
            "public_status": UserDocumentPublicStatus.PROCESSING,
            "internal_stage": UserDocumentInternalStage.EXTRACTION,
            "active_revision_id": None,
        },
        {
            "public_status": UserDocumentPublicStatus.FAILED,
            "internal_stage": None,
            "active_revision_id": None,
            "safe_error_code": "processing_failed",
        },
        {
            "public_status": UserDocumentPublicStatus.DELETING,
            "internal_stage": None,
            "enabled": False,
        },
    ),
    ids=("disabled", "processing-no-active", "failed", "deleting"),
)
def test_resolver_fails_closed_for_every_unusable_document_state(updates):
    store, ingestion = _materials()
    document = _ingest(ingestion)
    store.save_document(
        owner_principal_id=OWNER_A,
        document=document.model_copy(update=updates),
    )

    _assert_scope_unavailable(
        InterviewKnowledgeScopeResolver(store=store, clock=lambda: NOW),
        owner=OWNER_A,
        document_id=document.document_id,
    )


def test_prep_api_persists_owner_and_scope_but_projects_only_safe_refs(monkeypatch):
    store, ingestion = _materials()
    first = _ingest(ingestion, name="first.txt", content=b"first")
    second = _ingest(ingestion, name="second.txt", content=b"second")
    foreign = _ingest(
        ingestion,
        owner=OWNER_B,
        name="foreign.txt",
        content=b"foreign",
    )
    scope_resolver = InterviewKnowledgeScopeResolver(store=store, clock=lambda: NOW)
    revision_store = InMemoryInterviewPlanRevisionStore()
    prep_store = InMemoryPrepPlanStore(clock=lambda: NOW)
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=OWNER_A,
        clock=lambda: NOW,
    )
    calls = []

    def prepare(
        _job_description,
        _resume_text,
        *,
        execution_runner=None,
        configuration=None,
        knowledge_store=None,
        knowledge_scope=None,
    ):
        del execution_runner, knowledge_store
        calls.append(knowledge_scope)
        return bind_prepared_plan_revision(
            fallback_interview_plan(configuration),
            configuration,
            knowledge_scope=knowledge_scope,
        )

    monkeypatch.setattr(prep_route_module, "prepare_interview", prepare)
    monkeypatch.setattr(
        prep_route_module,
        "get_agent_execution_runner",
        lambda: None,
    )
    app.dependency_overrides[dependencies.get_prep_plan_store] = lambda: prep_store
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[dependencies.get_prep_knowledge_repository] = lambda: None
    app.dependency_overrides[
        dependencies.get_interview_knowledge_scope_resolver
    ] = lambda: scope_resolver
    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=True, ingest_enabled=True)

    try:
        response = TestClient(app).post(
            "/api/prep",
            json={
                "job_description": "Backend role",
                "resume_text": "Built backend systems",
                "knowledge_scope": {
                    "include_system_knowledge": False,
                    "selected_document_ids": [
                        second.document_id,
                        first.document_id,
                    ],
                },
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        revision = revision_store.get_by_id(body["plan_revision_id"])
        protected_source = revision_store.get_source(
            revision.source_id
        ).protected_payload
        assert revision.plan.knowledge_scope == calls[0]
        assert protected_source.owner_principal_id == OWNER_A
        assert body["plan"]["knowledge_scope"] == {
            "schema_version": "interview-knowledge-scope-v1",
            "include_system_knowledge": False,
            "selected_documents": [
                {"document_id": document_id}
                for document_id in sorted([first.document_id, second.document_id])
            ],
        }
        serialized = str(body["plan"]["knowledge_scope"])
        for forbidden in (
            "owner_principal_id",
            "document_revision_id",
            "content_sha256",
            "selection_sha256",
            "allowed_usages",
            "created_at",
        ):
            assert forbidden not in serialized

        invalid_requests = (
            {
                "include_system_knowledge": True,
                "selected_document_ids": [first.document_id, first.document_id],
            },
            {
                "include_system_knowledge": True,
                "selected_document_ids": [],
                "owner_principal_id": OWNER_A,
            },
        )
        for invalid_scope in invalid_requests:
            invalid = TestClient(app).post(
                "/api/prep",
                json={
                    "job_description": "Backend role",
                    "resume_text": "Built backend systems",
                    "knowledge_scope": invalid_scope,
                },
            )
            assert invalid.status_code == 422

        unavailable_responses = [
            TestClient(app).post(
                "/api/prep",
                json={
                    "job_description": "Backend role",
                    "resume_text": "Built backend systems",
                    "knowledge_scope": {
                        "include_system_knowledge": True,
                        "selected_document_ids": [document_id],
                    },
                },
            )
            for document_id in (
                "not-a-uuid",
                str(uuid4()),
                foreign.document_id,
            )
        ]
        assert [item.status_code for item in unavailable_responses] == [409, 409, 409]
        assert {
            item.json()["detail"]["code"] for item in unavailable_responses
        } == {"knowledge_scope_document_unavailable"}
        assert len({str(item.json()) for item in unavailable_responses}) == 1
    finally:
        app.dependency_overrides.clear()


def test_prep_api_keeps_explicit_empty_distinct_from_legacy_scope(monkeypatch):
    store, _ = _materials()
    scope_resolver = InterviewKnowledgeScopeResolver(store=store, clock=lambda: NOW)
    revision_store = InMemoryInterviewPlanRevisionStore()
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=OWNER_A,
        clock=lambda: NOW,
    )

    def prepare(
        _job_description,
        _resume_text,
        *,
        execution_runner=None,
        configuration=None,
        knowledge_scope=None,
        **_kwargs,
    ):
        del execution_runner
        return bind_prepared_plan_revision(
            fallback_interview_plan(configuration),
            configuration,
            knowledge_scope=knowledge_scope,
        )

    monkeypatch.setattr(prep_route_module, "prepare_interview", prepare)
    monkeypatch.setattr(prep_route_module, "get_agent_execution_runner", lambda: None)
    app.dependency_overrides[dependencies.get_prep_plan_store] = (
        lambda: InMemoryPrepPlanStore(clock=lambda: NOW)
    )
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[dependencies.get_prep_knowledge_repository] = lambda: None
    app.dependency_overrides[
        dependencies.get_interview_knowledge_scope_resolver
    ] = lambda: scope_resolver
    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=True, ingest_enabled=True)
    client = TestClient(app)

    try:
        explicit = client.post(
            "/api/prep",
            json={
                "job_description": "Backend role",
                "resume_text": "Built systems",
                "knowledge_scope": {
                    "include_system_knowledge": False,
                    "selected_document_ids": [],
                },
            },
        )
        legacy = client.post(
            "/api/prep",
            json={
                "job_description": "Backend role",
                "resume_text": "Built systems",
            },
        )

        assert explicit.status_code == legacy.status_code == 200
        explicit_revision = revision_store.get_by_id(
            explicit.json()["plan_revision_id"]
        )
        legacy_revision = revision_store.get_by_id(legacy.json()["plan_revision_id"])
        explicit_source = revision_store.get_source(
            explicit_revision.source_id
        ).protected_payload
        legacy_source = revision_store.get_source(
            legacy_revision.source_id
        ).protected_payload
        assert explicit_revision.plan.knowledge_scope.include_system_knowledge is False
        assert explicit_revision.plan.knowledge_scope.selected_documents == ()
        assert explicit_revision.plan.knowledge_scope.created_at == NOW
        assert explicit_source.owner_principal_id == OWNER_A
        assert legacy_revision.plan.knowledge_scope.include_system_knowledge is True
        assert legacy_revision.plan.knowledge_scope.selected_documents == ()
        assert legacy_revision.plan.knowledge_scope.created_at is None
        assert legacy_source.owner_principal_id is None
        assert "owner_principal_id" not in legacy_source.model_dump(mode="json")
        assert "owner_principal_id" not in str(explicit.json())
        assert "owner_principal_id" not in str(legacy.json())
    finally:
        app.dependency_overrides.clear()


def test_prep_api_does_not_construct_materials_resolver_for_empty_scope_when_disabled(
    monkeypatch,
):
    revision_store = InMemoryInterviewPlanRevisionStore()
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-disabled-test",
        principal_id=OWNER_A,
        clock=lambda: NOW,
    )
    generated_scopes = []
    resolver_calls = []

    def prepare(
        _job_description,
        _resume_text,
        *,
        execution_runner=None,
        configuration=None,
        knowledge_scope=None,
        **_kwargs,
    ):
        del execution_runner
        generated_scopes.append(knowledge_scope)
        return bind_prepared_plan_revision(
            fallback_interview_plan(configuration),
            configuration,
            knowledge_scope=knowledge_scope,
        )

    def unavailable_materials_resolver():
        resolver_calls.append(True)
        raise AssertionError("empty Scope must not construct the Materials resolver")

    monkeypatch.setattr(prep_route_module, "prepare_interview", prepare)
    monkeypatch.setattr(prep_route_module, "get_agent_execution_runner", lambda: None)
    app.dependency_overrides[dependencies.get_prep_plan_store] = (
        lambda: InMemoryPrepPlanStore(clock=lambda: NOW)
    )
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[dependencies.get_prep_knowledge_repository] = lambda: None
    monkeypatch.setattr(
        dependencies,
        "_get_interview_knowledge_scope_resolver",
        unavailable_materials_resolver,
    )
    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=False, ingest_enabled=False)
    client = TestClient(app)

    try:
        responses = [
            client.post(
                "/api/prep",
                json={
                    "job_description": "Backend role",
                    "resume_text": "Built systems",
                    "knowledge_scope": {
                        "include_system_knowledge": include_system_knowledge,
                        "selected_document_ids": [],
                    },
                },
            )
            for include_system_knowledge in (True, False)
        ]

        assert [response.status_code for response in responses] == [200, 200]
        assert resolver_calls == []
        assert [
            scope.include_system_knowledge for scope in generated_scopes
        ] == [True, False]
        assert all(scope.selected_documents == () for scope in generated_scopes)
        assert all(scope.created_at is not None for scope in generated_scopes)
        assert all(
            "owner_principal_id" not in str(response.json())
            for response in responses
        )
    finally:
        app.dependency_overrides.clear()


def test_prep_api_fails_closed_when_selected_scope_has_no_resolver(monkeypatch):
    generation_calls = []
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-missing-resolver-test",
        principal_id=OWNER_A,
        clock=lambda: NOW,
    )

    def forbidden_plan_generation(*_args, **_kwargs):
        generation_calls.append(True)
        raise AssertionError("selected Scope must fail before plan generation")

    monkeypatch.setattr(
        prep_route_module,
        "prepare_interview",
        forbidden_plan_generation,
    )
    app.dependency_overrides[dependencies.get_prep_plan_store] = (
        lambda: InMemoryPrepPlanStore(clock=lambda: NOW)
    )
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        InMemoryInterviewPlanRevisionStore
    )
    app.dependency_overrides[dependencies.get_prep_knowledge_repository] = lambda: None
    app.dependency_overrides[
        dependencies.get_interview_knowledge_scope_resolver
    ] = lambda: None
    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=True, ingest_enabled=False)

    try:
        document_id = str(uuid4())
        response = TestClient(app).post(
            "/api/prep",
            json={
                "job_description": "Backend role",
                "resume_text": "Built systems",
                "knowledge_scope": {
                    "include_system_knowledge": True,
                    "selected_document_ids": [document_id],
                },
            },
        )

        assert response.status_code == 404
        assert response.json() == {
            "detail": {"code": "not_found", "message": "未找到资源。"}
        }
        assert document_id not in response.text
        assert generation_calls == []
    finally:
        app.dependency_overrides.clear()
