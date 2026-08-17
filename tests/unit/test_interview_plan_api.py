from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.plans.routes as route_module
import app.api.shared.dependencies as shared_dependencies
from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
    InMemoryUserDocumentStore,
)
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.application.materials.deletion_service import UserDocumentDeletionService
from app.application.materials.service import UserDocumentService
from app.main import app
from app.services.interview_knowledge_scope import InterviewKnowledgeScopeResolver
from app.services.interview_plan_regenerator import PlanRegenerationFailed
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    PlanSourcePayload,
    build_interview_knowledge_scope_snapshot,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.session import InterviewSessionStore
from app.domain.interview.drafts import DraftWriteConflict
from app.services.in_memory_draft_store import InMemoryDraftStore
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from tests.vector_store_fixtures import FakeEmbeddingProvider
from tests.unit.test_interview_plan_revision import plan, source


class StubRegenerator:
    def __init__(self) -> None:
        self.question_calls = 0
        self.all_calls = 0
        self.all_configurations = []

    def regenerate_question(self, *, current, source, question_id):
        self.question_calls += 1
        return InterviewPlanQuestionV2(
            question_id=current.plan.questions[0].question_id,
            position=1,
            question_text="Explain a resilient cache invalidation strategy.",
            focus="cache resilience",
            question_type="technical",
            difficulty="advanced",
            expected_minutes=8,
            expected_followups=1,
            origin="generated",
        )

    def regenerate_all(self, *, current, source, configuration=None):
        self.all_calls += 1
        selected = configuration or current.configuration_snapshot
        self.all_configurations.append(selected)
        questions = tuple(
            question.model_copy(update={"difficulty": selected.difficulty})
            for question in current.plan.questions
        )
        return current.plan.model_copy(
            update={
                "title": "Provider regenerated plan",
                "configuration_snapshot": selected,
                "questions": questions,
            }
        )


class FailingRegenerator:
    def regenerate_question(self, **_kwargs):
        raise PlanRegenerationFailed("provider_timeout", "Provider regeneration timed out")


class FailOnceReferenceStore(InMemoryInterviewPlanRevisionStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_replace = False
        self.fail_next_add = False

    def replace_source_reference(self, **kwargs):
        if self.fail_next_replace:
            self.fail_next_replace = False
            raise RuntimeError("injected reference replacement failure")
        return super().replace_source_reference(**kwargs)

    def add_source_reference(self, *args, **kwargs):
        if self.fail_next_add:
            self.fail_next_add = False
            raise RuntimeError("injected reference add failure")
        return super().add_source_reference(*args, **kwargs)


class FailOnceCommitDraftStore(InMemoryDraftStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_commit = False

    def commit_save(self, draft):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("injected draft commit failure")
        return super().commit_save(draft)


class ConflictingDraftStore(InMemoryDraftStore):
    def commit_save(self, draft):
        raise DraftWriteConflict("injected concurrent write")


class ResponseLossSessionStore(InterviewSessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.lose_next_response = True

    def start(self, *args, **kwargs):
        turn = super().start(*args, **kwargs)
        if self.lose_next_response:
            self.lose_next_response = False
            raise RuntimeError("injected response loss")
        return turn


@pytest.fixture
def api_plan():
    store = InMemoryInterviewPlanRevisionStore()
    initial = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    regenerator = StubRegenerator()
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: store
    app.dependency_overrides[route_module.get_plan_regenerator] = lambda: regenerator
    app.dependency_overrides[route_module.get_draft_store] = lambda: InMemoryDraftStore()
    app.dependency_overrides[route_module.get_prep_plan_store] = (
        lambda: InMemoryPrepPlanStore()
    )
    try:
        yield TestClient(app), store, initial, regenerator
    finally:
        app.dependency_overrides.clear()


def edit_payload(revision, request_id, operation):
    return {
        "expected_revision": revision,
        "request_id": request_id,
        "operations": [operation],
    }


def test_edit_move_delete_restore_api_appends_monotonic_revisions(api_plan):
    client, store, initial, _ = api_plan
    first_id = initial.plan.questions[0].question_id

    edited = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "edit-1",
            {
                "op": "edit_question_text",
                "question_id": first_id,
                "question_text": "Explain cache stampede protection.",
            },
        ),
    )
    moved = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            2,
            "move-1",
            {"op": "move_question", "question_id": first_id, "to_position": 3},
        ),
    )
    added = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            3,
            "add-1",
            {
                "op": "add_custom_question",
                "question_text": "Describe a production incident review.",
                "focus": "incident learning",
                "question_type": "behavioral",
                "difficulty": "intermediate",
                "expected_minutes": 6,
                "expected_followups": 1,
            },
        ),
    )
    custom_id = added.json()["plan"]["questions"][-1]["question_id"]
    deleted = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            4,
            "delete-1",
            {"op": "delete_question", "question_id": custom_id},
        ),
    )
    restored = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            5,
            "restore-1",
            {"op": "restore_revision", "target_revision_id": initial.plan_revision_id},
        ),
    )

    assert edited.status_code == moved.status_code == added.status_code == 200
    assert deleted.status_code == restored.status_code == 200
    assert edited.json()["plan"]["questions"][0]["question_id"] == first_id
    assert moved.json()["plan"]["questions"][2]["question_id"] == first_id
    assert len(deleted.json()["plan"]["questions"]) == 3
    assert restored.json()["revision"] == 6
    assert restored.json()["plan_sha256"] == initial.plan_sha256
    assert edited.json()["audit"]["created_reason"] == "edit_question_text"
    assert edited.json()["audit"]["operations"][0]["actor"] == "user"
    assert edited.json()["audit"]["operations"][0][
        "knowledge_binding_action"
    ] == "invalidate"
    assert moved.json()["audit"]["operations"][0][
        "knowledge_binding_action"
    ] == "preserve"
    assert added.json()["audit"]["operations"][0][
        "knowledge_binding_action"
    ] == "unbound"
    assert restored.json()["audit"]["operations"][0][
        "knowledge_binding_action"
    ] == "restore"
    assert [item.revision for item in store.list_revisions(initial.plan_family_id)] == list(
        range(1, 7)
    )


def test_revision_history_api_returns_safe_newest_first_summaries(api_plan):
    client, _, initial, _ = api_plan
    edited = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "history-edit-1",
            {
                "op": "edit_question_text",
                "question_id": initial.plan.questions[0].question_id,
                "question_text": "Explain safe cache invalidation.",
            },
        ),
    )
    assert edited.status_code == 200

    response = client.get(
        f"/api/interview-plans/{initial.plan_family_id}/revisions"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_family_id"] == initial.plan_family_id
    assert payload["latest_revision"] == 2
    assert [item["revision"] for item in payload["revisions"]] == [2, 1]
    assert payload["revisions"][0]["is_latest"] is True
    assert payload["revisions"][1]["is_latest"] is False
    assert payload["revisions"][0]["question_count"] == 3
    assert set(payload["revisions"][0]) == {
        "plan_revision_id",
        "revision",
        "parent_revision_id",
        "plan_sha256",
        "created_at",
        "created_reason",
        "source_kind",
        "title",
        "question_count",
        "is_latest",
    }
    serialized = response.text
    assert "Explain safe cache invalidation." not in serialized
    assert "job_description" not in serialized
    assert "resume_text" not in serialized


def test_revision_history_api_rejects_unknown_family(api_plan):
    client, _, _, _ = api_plan

    response = client.get(
        "/api/interview-plans/00000000-0000-0000-0000-000000000000/revisions"
    )

    assert response.status_code == 404


def test_provider_question_regeneration_replaces_identity_and_is_idempotent(api_plan):
    client, store, initial, regenerator = api_plan
    old = initial.plan.questions[1]
    endpoint = (
        f"/api/interview-plans/{initial.plan_family_id}/questions/"
        f"{old.question_id}/regenerate"
    )
    payload = {"expected_revision": 1, "request_id": "regenerate-1"}

    first = client.post(endpoint, json=payload)
    replay = client.post(endpoint, json=payload)

    assert first.status_code == replay.status_code == 200
    assert replay.json()["plan_revision_id"] == first.json()["plan_revision_id"]
    replacement = first.json()["plan"]["questions"][1]
    assert replacement["question_id"] != old.question_id
    assert replacement["replaces_question_id"] == old.question_id
    assert replacement["origin"] == "regenerated"
    assert len(store.list_revisions(initial.plan_family_id)) == 2
    assert regenerator.question_calls == 1


def test_provider_timeout_does_not_create_or_advance_revision(api_plan):
    client, store, initial, _ = api_plan
    app.dependency_overrides[route_module.get_plan_regenerator] = lambda: FailingRegenerator()

    response = client.post(
        f"/api/interview-plans/{initial.plan_family_id}/questions/"
        f"{initial.plan.questions[0].question_id}/regenerate",
        json={"expected_revision": 1, "request_id": "timeout-1"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "provider_timeout"
    assert store.get_latest(initial.plan_family_id).plan_revision_id == initial.plan_revision_id
    assert len(store.list_revisions(initial.plan_family_id)) == 1


def test_revision_conflict_contains_current_metadata_and_does_not_overwrite(api_plan):
    client, store, initial, _ = api_plan
    response = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            99,
            "stale-1",
            {
                "op": "edit_focus",
                "question_id": initial.plan.questions[0].question_id,
                "focus": "stale change",
            },
        ),
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == {
        "plan_revision_id": initial.plan_revision_id,
        "revision": 1,
        "plan_sha256": initial.plan_sha256,
    }
    assert len(store.list_revisions(initial.plan_family_id)) == 1


def test_duplicate_request_and_structured_safety_error(api_plan):
    client, store, initial, _ = api_plan
    payload = edit_payload(
        1,
        "idempotent-edit",
        {
            "op": "edit_focus",
            "question_id": initial.plan.questions[0].question_id,
            "focus": "cache consistency",
        },
    )
    first = client.patch(f"/api/interview-plans/{initial.plan_family_id}", json=payload)
    replay = client.patch(f"/api/interview-plans/{initial.plan_family_id}", json=payload)
    two_questions = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            2,
            "delete-to-two",
            {"op": "delete_question", "question_id": initial.plan.questions[0].question_id},
        ),
    )
    two_question_payload = two_questions.json()
    one_questions = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            3,
            "delete-to-one",
            {
                "op": "delete_question",
                "question_id": two_question_payload["plan"]["questions"][0]["question_id"],
            },
        ),
    )
    one_question_payload = one_questions.json()
    minimum = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            4,
            "delete-last-question",
            {
                "op": "delete_question",
                "question_id": one_question_payload["plan"]["questions"][0]["question_id"],
            },
        ),
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["plan_revision_id"] == replay.json()["plan_revision_id"]
    assert two_questions.status_code == one_questions.status_code == 200
    assert len(two_question_payload["plan"]["questions"]) == 2
    assert len(one_question_payload["plan"]["questions"]) == 1
    for payload in (two_question_payload, one_question_payload):
        assessment = payload["budget_assessment"]
        assert assessment["launch_allowed"] is True
        assert "below_recommended_question_count" in assessment["warning_codes"]
    assert (
        one_question_payload["budget_assessment"]["estimate"]["estimated_minutes"]
        < two_question_payload["budget_assessment"]["estimate"]["estimated_minutes"]
    )
    assert minimum.status_code == 422
    assert minimum.json()["detail"]["code"] == "minimum_question_count"
    assert len(store.list_revisions(initial.plan_family_id)) == 4


def test_api_allows_ten_questions_and_rejects_the_eleventh(api_plan):
    client, store, initial, _ = api_plan
    latest_payload = None
    for expected_revision in range(1, 8):
        response = client.patch(
            f"/api/interview-plans/{initial.plan_family_id}",
            json=edit_payload(
                expected_revision,
                f"add-safe-{expected_revision}",
                {
                    "op": "add_custom_question",
                    "question_text": f"Explain distinct scenario {expected_revision}.",
                    "focus": "configured depth",
                    "question_type": "technical",
                    "difficulty": "intermediate",
                    "expected_minutes": 4,
                    "expected_followups": 1,
                },
            ),
        )
        assert response.status_code == 200
        latest_payload = response.json()

    assert latest_payload is not None
    assert len(latest_payload["plan"]["questions"]) == 10
    assert latest_payload["budget_assessment"]["launch_allowed"] is True
    assert latest_payload["budget_assessment"]["question_count"] == 10

    rejected = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            8,
            "add-unsafe-eleventh",
            {
                "op": "add_custom_question",
                "question_text": "Eleventh question",
                "focus": "too many",
                "question_type": "technical",
                "difficulty": "intermediate",
                "expected_minutes": 4,
                "expected_followups": 1,
            },
        ),
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "maximum_question_count"
    assert len(store.list_revisions(initial.plan_family_id)) == 8


def test_blank_edit_is_rejected_without_advancing_latest_revision(api_plan):
    client, store, initial, _ = api_plan
    response = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "blank-edit",
            {
                "op": "edit_question_text",
                "question_id": initial.plan.questions[0].question_id,
                "question_text": "   ",
            },
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_plan"
    assert store.get_latest(initial.plan_family_id).plan_revision_id == initial.plan_revision_id


def test_client_cannot_supply_provider_output_and_full_regeneration_requires_confirmation(
    api_plan,
):
    client, store, initial, regenerator = api_plan
    blocked = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "forged-provider-output",
            {
                "op": "regenerate_question",
                "question_id": initial.plan.questions[0].question_id,
                "question_text": "Client supplied replacement",
                "focus": "forged",
                "question_type": "technical",
                "difficulty": "intermediate",
                "expected_minutes": 5,
                "expected_followups": 0,
            },
        ),
    )
    unconfirmed = client.post(
        f"/api/interview-plans/{initial.plan_family_id}/regenerate",
        json={"expected_revision": 1, "request_id": "full-1", "confirmed": False},
    )
    confirmed = client.post(
        f"/api/interview-plans/{initial.plan_family_id}/regenerate",
        json={"expected_revision": 1, "request_id": "full-1", "confirmed": True},
    )

    assert blocked.status_code == 422
    assert blocked.json()["detail"]["code"] == "provider_managed_operation"
    assert unconfirmed.status_code == 422
    assert confirmed.status_code == 200
    assert confirmed.json()["revision"] == 2
    assert confirmed.json()["plan"]["title"] == "Provider regenerated plan"
    assert regenerator.all_calls == 1
    assert len(store.list_revisions(initial.plan_family_id)) == 2


def test_confirmed_regeneration_can_freeze_a_new_valid_configuration(api_plan):
    client, store, initial, regenerator = api_plan
    configuration = initial.configuration_snapshot.model_copy(
        update={
            "difficulty": "advanced",
            "focus_preset": "technical_depth",
        }
    )

    request_payload = {
        "expected_revision": 1,
        "request_id": "configured-regenerate-1",
        "confirmed": True,
        "configuration": configuration.model_dump(mode="json"),
    }
    response = client.post(
        f"/api/interview-plans/{initial.plan_family_id}/regenerate",
        json=request_payload,
    )
    replay = client.post(
        f"/api/interview-plans/{initial.plan_family_id}/regenerate",
        json=request_payload,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 2
    assert replay.status_code == 200
    assert replay.json()["plan_revision_id"] == payload["plan_revision_id"]
    assert payload["plan"]["configuration_snapshot"] == configuration.model_dump(
        mode="json"
    )
    assert payload["audit"]["configuration_diff"].keys() == {
        "difficulty",
        "focus_preset",
    }
    assert regenerator.all_configurations == [configuration]
    assert store.get_latest(initial.plan_family_id).configuration_snapshot == configuration
    assert len(store.list_revisions(initial.plan_family_id)) == 2


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("target_duration_minutes", 25),
        ("max_followups_per_question", 3),
        ("difficulty", "expert"),
        ("focus_preset", "freeform"),
    ],
)
def test_regeneration_rejects_configuration_outside_backend_contract(
    api_plan,
    field,
    invalid_value,
):
    client, store, initial, regenerator = api_plan
    configuration = initial.configuration_snapshot.model_dump(mode="json")
    configuration[field] = invalid_value

    response = client.post(
        f"/api/interview-plans/{initial.plan_family_id}/regenerate",
        json={
            "expected_revision": 1,
            "request_id": "invalid-configured-regenerate",
            "confirmed": True,
            "configuration": configuration,
        },
    )

    assert response.status_code == 422
    assert regenerator.all_calls == 0
    assert store.get_latest(initial.plan_family_id).revision == 1


class ProviderSpy:
    def __init__(self) -> None:
        self.calls = 0

    def generate_plan(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("interview start must not call the Provider")


def test_start_uses_verified_revision_snapshot_with_zero_provider_calls(api_plan):
    client, revision_store, initial, _ = api_plan
    provider = ProviderSpy()
    session_store = InterviewSessionStore(llm=provider)
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "start-verified-revision",
    }

    started = client.post("/api/interviews", json=payload)

    assert started.status_code == 200
    assert provider.calls == 0
    session_id = started.json()["session_id"]
    state = session_store.get(session_id)
    assert state["plan_origin"] == "plan_revision"
    assert state["plan_revision_id"] == initial.plan_revision_id
    assert state["plan_family_id"] == initial.plan_family_id
    assert state["revision"] == initial.revision
    assert state["plan_sha256"] == initial.plan_sha256
    assert state["configuration_snapshot"] == initial.configuration_snapshot.model_dump(
        mode="json"
    )
    assert state["plan_snapshot"] == initial.plan.model_dump(mode="json")
    assert state.get("owner_principal_id") is None
    assert any(
        ref.owner_type == "session" and ref.owner_id == session_id
        for ref in revision_store.list_source_references(initial.source_id)
    )


def test_duplicate_session_start_request_replays_one_business_session(api_plan):
    client, revision_store, initial, _ = api_plan
    provider = ProviderSpy()
    session_store = InterviewSessionStore(llm=provider)
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "duplicate-session-start",
    }

    first = client.post("/api/interviews", json=payload)
    replay = client.post("/api/interviews", json=payload)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert provider.calls == 0
    assert list(session_store._sessions) == [first.json()["session_id"]]
    session_refs = [
        ref
        for ref in revision_store.list_source_references(initial.source_id)
        if ref.owner_type == "session"
    ]
    assert len(session_refs) == 1


@pytest.mark.parametrize("material_change", ("disabled", "deleted"))
def test_scoped_start_replay_uses_frozen_binding_after_material_change(
    material_change,
):
    owner = "principal-a"
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    document_store = InMemoryUserDocumentStore()
    chunks = InMemoryUserDocumentChunkRepository()
    document = UserDocumentIngestionService(
        store=document_store,
        chunks=chunks,
        embedder=FakeEmbeddingProvider(),
        clock=lambda: now,
    ).ingest(
        owner_principal_id=owner,
        original_filename="scope.txt",
        media_type="text/plain",
        content=b"Frozen scope material",
    )
    scope_resolver = InterviewKnowledgeScopeResolver(
        store=document_store,
        clock=lambda: now,
    )
    scope = scope_resolver.resolve(
        owner_principal_id=owner,
        selected_document_ids=(document.document_id,),
        include_system_knowledge=False,
    )
    revision_store = InMemoryInterviewPlanRevisionStore()
    initial = revision_store.create_initial(
        source_payload=PlanSourcePayload(
            **source().model_dump(mode="json"),
            owner_principal_id=owner,
        ),
        plan=plan().model_copy(update={"knowledge_scope": scope}),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    session_store = InterviewSessionStore()
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=owner,
    )
    app.dependency_overrides[shared_dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[shared_dependencies.get_session_store] = (
        lambda: session_store
    )
    app.dependency_overrides[
        shared_dependencies.get_interview_knowledge_scope_resolver
    ] = lambda: scope_resolver
    app.dependency_overrides[shared_dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        shared_dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=True, ingest_enabled=True)
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "scoped-replay-after-disable",
    }

    try:
        client = TestClient(app)
        first = client.post("/api/interviews", json=payload)
        assert first.status_code == 200, first.text
        if material_change == "disabled":
            UserDocumentService(store=document_store, clock=lambda: now).set_enabled(
                owner_principal_id=owner,
                document_id=document.document_id,
                enabled=False,
            )
        else:
            UserDocumentDeletionService(
                store=document_store,
                chunks=chunks,
                clock=lambda: now,
            ).delete(
                owner_principal_id=owner,
                document_id=document.document_id,
            )

        replay = client.post("/api/interviews", json=payload)

        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert list(session_store._sessions) == [first.json()["session_id"]]

        fresh_start = client.post(
            "/api/interviews",
            json={
                **payload,
                "request_id": f"scoped-first-start-after-{material_change}",
            },
        )
        assert fresh_start.status_code == 409
        assert fresh_start.json()["detail"]["code"] == (
            "knowledge_scope_document_unavailable"
        )
        assert list(session_store._sessions) == [first.json()["session_id"]]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    (
        "protected_owner",
        "request_owner",
        "resolver_available",
        "expected_status",
        "expected_code",
    ),
    (
        ("principal-a", "principal-a", True, 200, None),
        (
            "principal-a",
            "principal-b",
            True,
            409,
            "knowledge_scope_document_unavailable",
        ),
        (
            None,
            "principal-a",
            True,
            409,
            "knowledge_scope_document_unavailable",
        ),
        (
            "principal-a",
            "principal-a",
            False,
            409,
            "knowledge_scope_document_unavailable",
        ),
    ),
    ids=(
        "current-owner",
        "different-principal",
        "missing-protected-owner",
        "missing-scope-resolver",
    ),
)
def test_scoped_start_requires_current_principal_to_match_protected_owner(
    protected_owner,
    request_owner,
    resolver_available,
    expected_status,
    expected_code,
):
    material_owner = "principal-a"
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    document_store = InMemoryUserDocumentStore()
    chunks = InMemoryUserDocumentChunkRepository()
    document = UserDocumentIngestionService(
        store=document_store,
        chunks=chunks,
        embedder=FakeEmbeddingProvider(),
        clock=lambda: now,
    ).ingest(
        owner_principal_id=material_owner,
        original_filename="owner-scope.txt",
        media_type="text/plain",
        content=b"Owner-bound scope",
    )
    scope_resolver = InterviewKnowledgeScopeResolver(
        store=document_store,
        clock=lambda: now,
    )
    scope = scope_resolver.resolve(
        owner_principal_id=material_owner,
        selected_document_ids=(document.document_id,),
        include_system_knowledge=True,
    )
    revision_store = InMemoryInterviewPlanRevisionStore()
    initial = revision_store.create_initial(
        source_payload=PlanSourcePayload(
            **source().model_dump(mode="json"),
            owner_principal_id=protected_owner,
        ),
        plan=plan().model_copy(update={"knowledge_scope": scope}),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    session_store = InterviewSessionStore()
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=request_owner,
    )
    app.dependency_overrides[shared_dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[shared_dependencies.get_session_store] = (
        lambda: session_store
    )
    app.dependency_overrides[
        shared_dependencies.get_interview_knowledge_scope_resolver
    ] = lambda: scope_resolver if resolver_available else None
    app.dependency_overrides[shared_dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        shared_dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=True, ingest_enabled=True)

    try:
        response = TestClient(app).post(
            "/api/interviews",
            json={
                "plan_revision_id": initial.plan_revision_id,
                "expected_revision": initial.revision,
                "plan_sha256": initial.plan_sha256,
                "request_id": f"owner-bound-{request_owner}",
            },
        )

        assert response.status_code == expected_status
        if expected_code is None:
            state = session_store.get(response.json()["session_id"])
            assert state["owner_principal_id"] == material_owner
        else:
            assert response.json()["detail"]["code"] == expected_code
            assert session_store._sessions == {}
    finally:
        app.dependency_overrides.clear()


def test_explicit_empty_scope_binds_owner_without_material_runtime():
    owner = "principal-a"
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    scope = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=False,
        selected_documents=(),
        created_at=now,
    )
    revision_store = InMemoryInterviewPlanRevisionStore()
    initial = revision_store.create_initial(
        source_payload=PlanSourcePayload(
            **source().model_dump(mode="json"),
            owner_principal_id=owner,
        ),
        plan=plan().model_copy(update={"knowledge_scope": scope}),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    session_store = InterviewSessionStore()
    principal = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=owner,
    )
    app.dependency_overrides[shared_dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[shared_dependencies.get_session_store] = (
        lambda: session_store
    )
    app.dependency_overrides[
        shared_dependencies.get_interview_knowledge_scope_resolver
    ] = lambda: None
    app.dependency_overrides[shared_dependencies.get_principal_identity_resolver] = (
        lambda: principal
    )
    app.dependency_overrides[
        shared_dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=False, ingest_enabled=False)

    try:
        response = TestClient(app).post(
            "/api/interviews",
            json={
                "plan_revision_id": initial.plan_revision_id,
                "expected_revision": initial.revision,
                "plan_sha256": initial.plan_sha256,
                "request_id": "explicit-empty-owner-binding",
            },
        )

        assert response.status_code == 200, response.text
        state = session_store.get(response.json()["session_id"])
        assert state["owner_principal_id"] == owner
        assert state["plan_snapshot"]["knowledge_scope"] == scope.model_dump(
            mode="json"
        )
    finally:
        app.dependency_overrides.clear()


def test_session_memory_ignore_is_bound_before_start_and_replays(api_plan, monkeypatch):
    client, _, initial, _ = api_plan
    controls = InMemoryPrincipalMemoryControlStore()
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
    )
    for name, value in {
        "MEMORY_LONG_TERM_MODE": "local_consume",
        "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)

    class AssertIgnoredBeforeStartStore(InterviewSessionStore):
        def start(self, *args, **kwargs):
            control = controls.get_session(
                deployment_id="single-tenant-local",
                principal_id="local-owner",
                session_id=kwargs["session_id"],
            )
            assert control is not None and control.enabled is False
            return super().start(*args, **kwargs)

    session_store = AssertIgnoredBeforeStartStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    app.dependency_overrides[
        shared_dependencies.get_principal_memory_control_store
    ] = lambda: controls
    app.dependency_overrides[
        shared_dependencies.get_principal_identity_resolver
    ] = lambda: resolver
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "session-memory-ignore",
        "principal_memory_mode": "ignore",
    }

    first = client.post("/api/interviews", json=payload)
    replay = client.post("/api/interviews", json=payload)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    state = session_store.get(first.json()["session_id"])
    assert state["principal_memory_mode"] == "ignore"


def test_session_memory_choice_change_conflicts(api_plan, monkeypatch):
    client, _, initial, _ = api_plan
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "false")
    session_store = InterviewSessionStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "session-memory-choice-conflict",
        "principal_memory_mode": "inherit",
    }

    first = client.post("/api/interviews", json=payload)
    changed = client.post(
        "/api/interviews",
        json={**payload, "principal_memory_mode": "ignore"},
    )

    assert first.status_code == 200
    assert changed.status_code == 409
    assert changed.json() == {"code": "session_start_request_conflict"}


def test_session_memory_choice_is_rejected_outside_revision_launch(api_plan):
    client, _, _, _ = api_plan

    response = client.post(
        "/api/interviews",
        json={
            "job_description": "backend role",
            "resume_text": "python experience",
            "principal_memory_mode": "ignore",
        },
    )

    assert response.status_code == 422


def test_session_start_repairs_response_loss_without_missing_reference(api_plan):
    client, revision_store, initial, _ = api_plan
    session_store = ResponseLossSessionStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "response-loss-session-start",
    }

    response = client.post("/api/interviews", json=payload)

    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert session_store.get(session_id)["session_id"] == session_id
    assert any(
        ref.owner_type == "session" and ref.owner_id == session_id
        for ref in revision_store.list_source_references(initial.source_id)
    )


def test_session_start_reference_failure_creates_no_session_and_retry_recovers():
    revision_store = FailOnceReferenceStore()
    initial = revision_store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    session_store = InterviewSessionStore()
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: revision_store
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "reference-failure-session-start",
    }
    try:
        client = TestClient(app, raise_server_exceptions=False)
        revision_store.fail_next_add = True
        failed = client.post("/api/interviews", json=payload)
        assert failed.status_code == 500
        assert session_store._sessions == {}

        recovered = client.post("/api/interviews", json=payload)
        assert recovered.status_code == 200
        session_id = recovered.json()["session_id"]
        assert any(
            ref.owner_type == "session" and ref.owner_id == session_id
            for ref in revision_store.list_source_references(initial.source_id)
        )
    finally:
        app.dependency_overrides.clear()


def test_session_start_request_id_reuse_with_new_revision_is_conflict(api_plan):
    client, _, initial, _ = api_plan
    session_store = InterviewSessionStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    request_id = "session-start-conflict"
    first = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": initial.plan_revision_id,
            "expected_revision": initial.revision,
            "plan_sha256": initial.plan_sha256,
            "request_id": request_id,
        },
    )
    edited = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "edit-after-idempotent-start",
            {
                "op": "edit_focus",
                "question_id": initial.plan.questions[0].question_id,
                "focus": "new revision with reused start identity",
            },
        ),
    )
    replay_with_changed_input = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": edited.json()["plan_revision_id"],
            "expected_revision": edited.json()["revision"],
            "plan_sha256": edited.json()["plan_sha256"],
            "request_id": request_id,
        },
    )

    assert first.status_code == edited.status_code == 200
    assert replay_with_changed_input.status_code == 409
    assert replay_with_changed_input.json() == {
        "code": "session_start_request_conflict"
    }
    assert list(session_store._sessions) == [first.json()["session_id"]]


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("expected_revision", 2),
        ("plan_sha256", "0" * 64),
    ],
)
def test_session_start_request_id_reuse_with_changed_contract_is_conflict(
    api_plan,
    field,
    changed_value,
):
    client, _, initial, _ = api_plan
    session_store = InterviewSessionStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    payload = {
        "plan_revision_id": initial.plan_revision_id,
        "expected_revision": initial.revision,
        "plan_sha256": initial.plan_sha256,
        "request_id": "session-start-contract-conflict",
    }

    first = client.post("/api/interviews", json=payload)
    changed_payload = {**payload, field: changed_value}
    replay_with_changed_contract = client.post(
        "/api/interviews",
        json=changed_payload,
    )

    assert first.status_code == 200
    assert replay_with_changed_contract.status_code == 409
    assert replay_with_changed_contract.json() == {
        "code": "session_start_request_conflict"
    }
    assert list(session_store._sessions) == [first.json()["session_id"]]


def test_started_session_does_not_follow_later_plan_edits(api_plan):
    client, revision_store, initial, _ = api_plan
    session_store = InterviewSessionStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    started = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": initial.plan_revision_id,
            "expected_revision": 1,
            "plan_sha256": initial.plan_sha256,
            "request_id": "start-before-edit",
        },
    ).json()
    original_snapshot = session_store.get(started["session_id"])["plan_snapshot"]

    client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "post-start-edit",
            {
                "op": "edit_focus",
                "question_id": initial.plan.questions[0].question_id,
                "focus": "changed after start",
            },
        ),
    )

    assert revision_store.get_latest(initial.plan_family_id).revision == 2
    assert session_store.get(started["session_id"])["plan_snapshot"] == original_snapshot
    assert original_snapshot == initial.plan.model_dump(mode="json")


def test_start_rejects_a_historical_revision_and_returns_latest_winner(api_plan):
    client, _, initial, _ = api_plan
    session_store = InterviewSessionStore()
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    edited = client.patch(
        f"/api/interview-plans/{initial.plan_family_id}",
        json=edit_payload(
            1,
            "advance-before-start",
            {
                "op": "edit_focus",
                "question_id": initial.plan.questions[0].question_id,
                "focus": "latest server focus",
            },
        ),
    )

    started = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": initial.plan_revision_id,
            "expected_revision": initial.revision,
            "plan_sha256": initial.plan_sha256,
            "request_id": "start-historical-revision",
        },
    )

    assert edited.status_code == 200
    assert started.status_code == 409
    assert started.json() == {
        "code": "plan_revision_conflict",
        "current_revision": {
            "plan_revision_id": edited.json()["plan_revision_id"],
            "revision": 2,
            "plan_sha256": edited.json()["plan_sha256"],
        },
    }
    assert session_store._sessions == {}


def test_start_rejects_raw_inputs_mismatch_and_missing_revision(api_plan):
    client, _, initial, _ = api_plan
    app.dependency_overrides[route_module.get_session_store] = lambda: InterviewSessionStore()
    raw = client.post(
        "/api/interviews",
        json={"job_description": "JD", "resume_text": "Resume"},
    )
    revision_mismatch = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": initial.plan_revision_id,
            "expected_revision": 99,
            "plan_sha256": initial.plan_sha256,
            "request_id": "start-revision-mismatch",
        },
    )
    hash_mismatch = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": initial.plan_revision_id,
            "expected_revision": 1,
            "plan_sha256": "0" * 64,
            "request_id": "start-hash-mismatch",
        },
    )
    missing = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": "00000000-0000-0000-0000-000000000000",
            "expected_revision": 1,
            "plan_sha256": "0" * 64,
            "request_id": "start-missing-revision",
        },
    )

    assert raw.status_code == 422
    assert revision_mismatch.status_code == 409
    assert hash_mismatch.status_code == 409
    assert missing.status_code == 404


def test_draft_restores_exact_revision_and_source_edits_mark_it_stale(api_plan):
    client, revision_store, initial, _ = api_plan
    draft_store = InMemoryDraftStore()
    app.dependency_overrides[route_module.get_draft_store] = lambda: draft_store
    source_payload = source()
    created = client.post(
        "/api/interview-drafts",
        json={
            "job_description": source_payload.job_description,
            "resume_text": source_payload.resume_text,
            "job_tags": list(source_payload.job_tags),
            "plan_family_id": initial.plan_family_id,
            "latest_plan_revision_id": initial.plan_revision_id,
        },
    )
    restored_revision = client.get(
        f"/api/interview-plans/{initial.plan_family_id}/revisions/"
        f"{initial.plan_revision_id}"
    )
    edited = client.post(
        "/api/interview-drafts",
        json={
            "draft_id": created.json()["draft_id"],
            "job_description": source_payload.job_description + " changed",
            "resume_text": source_payload.resume_text,
            "job_tags": list(source_payload.job_tags),
        },
    )

    assert created.status_code == 200
    assert created.json()["plan_status"] == "active"
    assert any(
        ref.owner_type == "draft" and ref.owner_id == created.json()["draft_id"]
        for ref in revision_store.list_source_references(initial.source_id)
    )
    assert restored_revision.status_code == 200
    assert restored_revision.json()["plan_revision_id"] == initial.plan_revision_id
    assert restored_revision.json()["plan_sha256"] == initial.plan_sha256
    assert edited.status_code == 200
    assert edited.json()["plan_status"] == "stale"
    assert edited.json()["latest_plan_revision_id"] == initial.plan_revision_id

    deleted = client.delete(
        f"/api/interview-drafts/{created.json()['draft_id']}"
    )
    assert deleted.status_code == 204
    assert not any(
        ref.owner_type == "draft" and ref.owner_id == created.json()["draft_id"]
        for ref in revision_store.list_source_references(initial.source_id)
    )


def test_draft_reference_failure_is_retryable_without_mutating_draft():
    revision_store = FailOnceReferenceStore()
    initial = revision_store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    draft_store = InMemoryDraftStore()
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: revision_store
    app.dependency_overrides[route_module.get_draft_store] = lambda: draft_store
    app.dependency_overrides[route_module.get_prep_plan_store] = (
        lambda: InMemoryPrepPlanStore()
    )
    try:
        client = TestClient(app, raise_server_exceptions=False)
        revision_store.fail_next_replace = True
        body = {
            "draft_id": "draft_retryable",
            "job_description": source().job_description,
            "resume_text": source().resume_text,
            "job_tags": list(source().job_tags),
            "plan_family_id": initial.plan_family_id,
            "latest_plan_revision_id": initial.plan_revision_id,
        }
        failed = client.post("/api/interview-drafts", json=body)
        assert failed.status_code == 500
        with pytest.raises(ValueError, match="draft not found"):
            draft_store.get("draft_retryable")

        recovered = client.post("/api/interview-drafts", json=body)
        assert recovered.status_code == 200
        assert any(
            ref.owner_type == "draft" and ref.owner_id == "draft_retryable"
            for ref in revision_store.list_source_references(initial.source_id)
        )
    finally:
        app.dependency_overrides.clear()


def test_draft_write_conflict_maps_to_stable_http_409():
    revision_store = InMemoryInterviewPlanRevisionStore()
    app.dependency_overrides[route_module.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[route_module.get_draft_store] = (
        lambda: ConflictingDraftStore()
    )
    try:
        response = TestClient(app).post(
            "/api/interview-drafts",
            json={
                "draft_id": "draft-conflict",
                "job_description": "Backend role",
                "resume_text": "Built APIs",
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {"code": "draft_write_conflict"}
    finally:
        app.dependency_overrides.clear()


def test_draft_commit_failure_compensates_reference_and_delete_retry_succeeds():
    revision_store = FailOnceReferenceStore()
    initial = revision_store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    draft_store = FailOnceCommitDraftStore()
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: revision_store
    app.dependency_overrides[route_module.get_draft_store] = lambda: draft_store
    app.dependency_overrides[route_module.get_prep_plan_store] = (
        lambda: InMemoryPrepPlanStore()
    )
    body = {
        "draft_id": "draft_compensated",
        "job_description": source().job_description,
        "resume_text": source().resume_text,
        "job_tags": list(source().job_tags),
        "plan_family_id": initial.plan_family_id,
        "latest_plan_revision_id": initial.plan_revision_id,
    }
    try:
        client = TestClient(app, raise_server_exceptions=False)
        draft_store.fail_next_commit = True
        assert client.post("/api/interview-drafts", json=body).status_code == 500
        assert not any(
            ref.owner_type == "draft" and ref.owner_id == "draft_compensated"
            for ref in revision_store.list_source_references(initial.source_id)
        )
        assert client.post("/api/interview-drafts", json=body).status_code == 200

        revision_store.fail_next_replace = True
        assert client.delete("/api/interview-drafts/draft_compensated").status_code == 500
        assert draft_store.get("draft_compensated")["draft_id"] == "draft_compensated"
        assert client.delete("/api/interview-drafts/draft_compensated").status_code == 204
        assert not any(
            ref.owner_type == "draft" and ref.owner_id == "draft_compensated"
            for ref in revision_store.list_source_references(initial.source_id)
        )
    finally:
        app.dependency_overrides.clear()


def test_next_worker_draft_operation_preserves_unknown_draft_reference():
    revision_store = InMemoryInterviewPlanRevisionStore()
    initial = revision_store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    # A process-local draft snapshot is not authoritative for other workers.
    revision_store.add_source_reference(
        initial.source_id, owner_type="draft", owner_id="crashed-draft"
    )
    draft_store = InMemoryDraftStore()
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: revision_store
    app.dependency_overrides[route_module.get_draft_store] = lambda: draft_store
    try:
        response = TestClient(app).post(
            "/api/interview-drafts",
            json={
                "draft_id": "next-draft",
                "job_description": source().job_description,
                "resume_text": source().resume_text,
                "job_tags": list(source().job_tags),
                "plan_family_id": initial.plan_family_id,
                "latest_plan_revision_id": initial.plan_revision_id,
            },
        )
        assert response.status_code == 200
        refs = revision_store.list_source_references(initial.source_id)
        assert any(ref.owner_id == "crashed-draft" for ref in refs)
        assert any(
            ref.owner_type == "draft" and ref.owner_id == "next-draft"
            for ref in refs
        )
    finally:
        app.dependency_overrides.clear()
