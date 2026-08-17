from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.memory.user_documents import InMemoryUserDocumentStore
from app.api.prep import routes as prep_routes
from app.api.shared import dependencies
from app.api.shared.models import PrepRequest, StartInterviewRequest
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentPublicStatus,
    UserDocumentRevision,
)
from app.main import app
from app.runtime.config.models import UserMaterialsRuntimeSettings
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.interview_knowledge_scope import InterviewKnowledgeScopeResolver
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
    plan_payload_sha256,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.prep import fallback_interview_plan
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.session import InterviewSessionStore


NOW = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"


def _add_ready_document(
    store: InMemoryUserDocumentStore,
    *,
    owner: str = OWNER_A,
) -> tuple[UserDocument, UserDocumentRevision]:
    document_id = str(uuid4())
    revision_id = str(uuid4())
    document = UserDocument(
        document_id=document_id,
        owner_principal_id=owner,
        display_title="Private system design notes",
        original_filename="system-design.md",
        media_type="text/markdown",
        size_bytes=128,
        public_status=UserDocumentPublicStatus.READY,
        enabled=True,
        active_revision_id=revision_id,
        created_at=NOW,
        updated_at=NOW,
    )
    revision = UserDocumentRevision(
        document_revision_id=revision_id,
        document_id=document_id,
        revision=1,
        original_file_sha256="a" * 64,
        content_sha256="b" * 64,
        extracted_text_ref=f"memory:user-material:{revision_id}",
        parser_version="utf8-text-v1",
        chunker_version="paragraph-v1",
        embedding_identity="fake:embedding:test-v1:3",
        created_at=NOW,
    )
    store.create_document(owner_principal_id=owner, document=document)
    store.create_revision(
        owner_principal_id=owner,
        revision=revision,
        original_content=b"private system design notes",
        extracted_text="private system design notes",
    )
    return document, revision


def _identity(owner: str):
    return ExplicitPrincipalIdentityResolver(
        deployment_id="scope-contract",
        principal_id=owner,
        clock=lambda: NOW,
    )


def _override_runtime(
    *,
    document_store: InMemoryUserDocumentStore,
    revision_store: InMemoryInterviewPlanRevisionStore,
    session_store: InterviewSessionStore,
) -> None:
    resolver = InterviewKnowledgeScopeResolver(
        store=document_store,
        clock=lambda: NOW,
    )
    app.dependency_overrides[dependencies.get_interview_knowledge_scope_resolver] = (
        lambda: resolver
    )
    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: _identity(OWNER_A)
    )
    app.dependency_overrides[dependencies.get_user_materials_runtime_settings] = (
        lambda: UserMaterialsRuntimeSettings(enabled=True, ingest_enabled=False)
    )
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[dependencies.get_prep_plan_store] = (
        InMemoryPrepPlanStore
    )
    app.dependency_overrides[dependencies.get_session_store] = lambda: session_store
    app.dependency_overrides[dependencies.get_prep_knowledge_repository] = lambda: None


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "internal_field",
    [
        "owner_principal_id",
        "document_revision_id",
        "content_sha256",
        "allowed_usages",
        "display_title",
    ],
)
def test_prep_scope_request_rejects_internal_material_metadata(internal_field):
    payload = {
        "job_description": "Backend role",
        "resume_text": "Built distributed systems",
        "knowledge_scope": {
            "include_system_knowledge": True,
            "selected_document_ids": [str(uuid4())],
            internal_field: "untrusted-client-value",
        },
    }

    with pytest.raises(ValidationError):
        PrepRequest.model_validate(payload)


def test_start_request_rejects_client_supplied_scope():
    with pytest.raises(ValidationError):
        StartInterviewRequest.model_validate(
            {
                "plan_revision_id": str(uuid4()),
                "expected_revision": 1,
                "plan_sha256": "a" * 64,
                "request_id": "start-scope-contract",
                "knowledge_scope": {"selected_document_ids": [str(uuid4())]},
            }
        )


def test_invalid_prep_scope_stops_before_plan_generation(monkeypatch):
    calls = []
    document_store = InMemoryUserDocumentStore()
    _override_runtime(
        document_store=document_store,
        revision_store=InMemoryInterviewPlanRevisionStore(),
        session_store=InterviewSessionStore(),
    )

    def forbidden_plan_generation(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("invalid scope must fail before plan generation")

    monkeypatch.setattr(
        prep_routes,
        "prepare_interview",
        forbidden_plan_generation,
    )

    response = TestClient(app).post(
        "/api/prep",
        json={
            "job_description": "Backend role",
            "resume_text": "Built distributed systems",
            "knowledge_scope": {
                "include_system_knowledge": True,
                "selected_document_ids": [str(uuid4())],
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "knowledge_scope_document_unavailable"
    )
    assert calls == []


def test_prep_start_and_replay_preserve_one_authoritative_scope(monkeypatch):
    document_store = InMemoryUserDocumentStore()
    selected_document, selected_revision = _add_ready_document(document_store)
    revision_store = InMemoryInterviewPlanRevisionStore()
    session_store = InterviewSessionStore()
    _override_runtime(
        document_store=document_store,
        revision_store=revision_store,
        session_store=session_store,
    )
    generated_scopes = []

    def generate_without_provider(
        _job_description,
        _resume_text,
        *,
        execution_runner=None,
        configuration=None,
        knowledge_scope=None,
    ):
        del execution_runner
        generated_scopes.append(knowledge_scope)
        return fallback_interview_plan(configuration)

    monkeypatch.setattr(
        prep_routes,
        "prepare_interview",
        generate_without_provider,
    )
    client = TestClient(app)
    prepared = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role",
            "resume_text": "Built distributed systems",
            "knowledge_scope": {
                "include_system_knowledge": False,
                "selected_document_ids": [selected_document.document_id],
            },
        },
    )

    assert prepared.status_code == 200, prepared.text
    prepared_payload = prepared.json()
    saved = revision_store.get_by_id(prepared_payload["plan_revision_id"])
    protected_source = revision_store.get_source(saved.source_id).protected_payload
    assert generated_scopes == [saved.plan.knowledge_scope]
    assert protected_source.owner_principal_id == OWNER_A
    frozen = saved.plan.knowledge_scope.selected_documents[0]
    assert frozen.document_id == selected_document.document_id
    assert frozen.document_revision_id == selected_revision.document_revision_id
    assert frozen.content_sha256 == selected_revision.content_sha256
    assert frozen.allowed_usages == selected_document.allowed_usages
    assert saved.plan.knowledge_scope.created_at == NOW

    public_scope = prepared_payload["plan"]["knowledge_scope"]
    assert public_scope["selected_documents"] == [
        {"document_id": selected_document.document_id}
    ]
    serialized_public_payload = json.dumps(prepared_payload, sort_keys=True)
    for internal_name in (
        "document_revision_id",
        "content_sha256",
        "allowed_usages",
        "selection_sha256",
        "owner_principal_id",
    ):
        assert internal_name not in serialized_public_payload

    different_scope = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=True,
        selected_documents=saved.plan.knowledge_scope.selected_documents,
        created_at=NOW,
    )
    assert plan_payload_sha256(
        saved.plan.model_copy(update={"knowledge_scope": different_scope})
    ) != plan_payload_sha256(saved.plan)

    _add_ready_document(document_store)
    start_payload = {
        "plan_revision_id": saved.plan_revision_id,
        "expected_revision": saved.revision,
        "plan_sha256": saved.plan_sha256,
        "request_id": "start-authoritative-scope",
    }
    started = client.post("/api/interviews", json=start_payload)
    replay = client.post("/api/interviews", json=start_payload)

    assert started.status_code == replay.status_code == 200
    assert replay.json() == started.json()
    session_state = session_store.get(started.json()["session_id"])
    assert session_state["plan_snapshot"] == saved.plan.model_dump(mode="json")
    assert session_state["plan_snapshot"]["knowledge_scope"] == (
        saved.plan.knowledge_scope.model_dump(mode="json")
    )
    assert len(
        session_state["plan_snapshot"]["knowledge_scope"]["selected_documents"]
    ) == 1

    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: _identity(OWNER_B)
    )
    cross_owner_replay = client.post("/api/interviews", json=start_payload)
    assert cross_owner_replay.status_code == 409
    assert cross_owner_replay.json()["detail"]["code"] == (
        "knowledge_scope_document_unavailable"
    )

    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: _identity(OWNER_A)
    )
    document_store.save_document(
        owner_principal_id=OWNER_A,
        document=selected_document.model_copy(
            update={
                "public_status": UserDocumentPublicStatus.DISABLED,
                "enabled": False,
            }
        ),
    )
    disabled_replay = client.post("/api/interviews", json=start_payload)
    assert disabled_replay.status_code == 200
    assert disabled_replay.json() == started.json()

    assert document_store.delete_document(
        owner_principal_id=OWNER_A,
        document_id=selected_document.document_id,
    ) is not None
    deleted_replay = client.post("/api/interviews", json=start_payload)
    assert deleted_replay.status_code == 200
    assert deleted_replay.json() == started.json()


def test_legacy_plan_scope_is_deterministic_system_only_compatibility():
    legacy = fallback_interview_plan()
    from app.services.prep import prepared_plan_revision

    revision_plan = prepared_plan_revision(legacy)

    assert revision_plan.knowledge_scope.include_system_knowledge is True
    assert revision_plan.knowledge_scope.selected_documents == ()
    assert revision_plan.knowledge_scope.created_at is None
