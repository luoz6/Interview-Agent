import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.main import app
from app.services.interview_plan_regenerator import PlanRegenerationFailed
from app.services.interview_plan_revision import InterviewPlanQuestionV2
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.session import InterviewSessionStore
from app.services.drafts import AnonymousDraftStore
from tests.test_interview_plan_revision import plan, source


class StubRegenerator:
    def __init__(self) -> None:
        self.question_calls = 0
        self.all_calls = 0

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

    def regenerate_all(self, *, current, source):
        self.all_calls += 1
        return current.plan.model_copy(update={"title": "Provider regenerated plan"})


class FailingRegenerator:
    def regenerate_question(self, **_kwargs):
        raise PlanRegenerationFailed("provider_timeout", "Provider regeneration timed out")


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
    app.dependency_overrides[route_module.get_draft_store] = lambda: AnonymousDraftStore()
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
    assert [item.revision for item in store.list_revisions(initial.plan_family_id)] == list(
        range(1, 7)
    )


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
    assert any(
        ref.owner_type == "session" and ref.owner_id == session_id
        for ref in revision_store.list_source_references(initial.source_id)
    )


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
        },
    )
    hash_mismatch = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": initial.plan_revision_id,
            "expected_revision": 1,
            "plan_sha256": "0" * 64,
        },
    )
    missing = client.post(
        "/api/interviews",
        json={
            "plan_revision_id": "00000000-0000-0000-0000-000000000000",
            "expected_revision": 1,
            "plan_sha256": "0" * 64,
        },
    )

    assert raw.status_code == 422
    assert revision_mismatch.status_code == 409
    assert hash_mismatch.status_code == 409
    assert missing.status_code == 404


def test_draft_restores_exact_revision_and_source_edits_mark_it_stale(api_plan):
    client, revision_store, initial, _ = api_plan
    draft_store = AnonymousDraftStore()
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
