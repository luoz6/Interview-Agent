from __future__ import annotations

from collections import Counter
from itertools import cycle, islice
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.main import app
from app.services.drafts import AnonymousDraftStore
from app.services.interview_plan_budget import QUESTION_TYPE_ORDER
from app.services.interview_plan_regenerator import PlanRegenerationFailed
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    PlanConfigurationSnapshot,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    prepare_interview,
    public_interview_plan_v2_payload,
)
from app.services.session import InterviewSessionStore


PROFILE_QUESTION_COUNTS = {15: 3, 30: 5, 45: 7, 60: 9}
FOCUS_TYPE_ORDER = {
    "technical_depth": (
        "technical",
        "project",
        "system-design",
        "behavioral",
    ),
    "system_design": (
        "system-design",
        "technical",
        "project",
        "behavioral",
    ),
    "project_review": (
        "project",
        "technical",
        "behavioral",
        "system-design",
    ),
    "balanced": (
        "project",
        "technical",
        "system-design",
        "behavioral",
    ),
}


def configured_snapshot(
    *,
    duration: int = 30,
    difficulty: str = "intermediate",
    focus: str = "balanced",
    question_count: int | None = None,
) -> PlanConfigurationSnapshot:
    count = question_count or PROFILE_QUESTION_COUNTS[duration]
    ordered_types = list(islice(cycle(FOCUS_TYPE_ORDER[focus]), count))
    return PlanConfigurationSnapshot(
        difficulty=difficulty,
        target_duration_minutes=duration,
        focus_preset=focus,
        question_type_budget=dict(Counter(ordered_types)),
        expected_followup_budget=count,
        generator_version="plan-generator-v2",
        followup_policy_version="fixed_v1",
    )


def plan_for_configuration(
    configuration: PlanConfigurationSnapshot,
    *,
    omit_last: bool = False,
) -> InterviewPlan:
    kinds = [
        question_type
        for question_type in QUESTION_TYPE_ORDER
        for _ in range(configuration.question_type_budget.get(question_type, 0))
    ]
    if omit_last:
        kinds.pop()
    return InterviewPlan(
        title=(
            f"{configuration.target_duration_minutes}-minute "
            f"{configuration.difficulty} {configuration.focus_preset} plan"
        ),
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind=kind,
                prompt=(
                    f"Configured {configuration.difficulty} "
                    f"{configuration.focus_preset} {kind} question {index}"
                ),
                focus=f"{configuration.focus_preset} focus {index}",
            )
            for index, kind in enumerate(kinds, start=1)
        ],
    )


class PrepProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.available = True
        self.omit_last = False

    def generate_plan(
        self,
        _job_description,
        _resume_text,
        knowledge_context=None,
        configuration=None,
    ):
        del knowledge_context
        self.calls += 1
        if not self.available:
            raise RuntimeError("Provider is unavailable")
        return plan_for_configuration(
            configuration,
            omit_last=self.omit_last,
        )


class StartProviderSpy:
    def __init__(self) -> None:
        self.calls = 0

    def generate_plan(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("interview start must not call the Provider")


class ConfiguredRegenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.available = True

    def regenerate_question(self, *, current, source, question_id):
        del source
        self.calls += 1
        if not self.available:
            raise PlanRegenerationFailed(
                "provider_unavailable",
                "Provider is unavailable",
            )
        existing = next(
            question
            for question in current.plan.questions
            if question.question_id == question_id
        )
        return InterviewPlanQuestionV2(
            question_id=existing.question_id,
            position=existing.position,
            question_text="Regenerated configured interview question.",
            focus="regenerated configured focus",
            question_type=existing.question_type,
            difficulty=existing.difficulty,
            expected_minutes=existing.expected_minutes,
            expected_followups=existing.expected_followups,
            origin="generated",
        )


@pytest.fixture
def configured_api(monkeypatch):
    revision_store = InMemoryInterviewPlanRevisionStore()
    prep_provider = PrepProvider()
    start_provider = StartProviderSpy()
    session_store = InterviewSessionStore(llm=start_provider)
    draft_store = AnonymousDraftStore()
    regenerator = ConfiguredRegenerator()

    app.dependency_overrides[route_module.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[route_module.get_session_store] = lambda: session_store
    app.dependency_overrides[route_module.get_draft_store] = lambda: draft_store
    app.dependency_overrides[route_module.get_plan_regenerator] = lambda: regenerator
    monkeypatch.setattr(route_module, "get_runtime_store", lambda: "memory")
    monkeypatch.setattr(
        route_module,
        "prepare_interview",
        lambda job_description,
        resume_text,
        execution_runner=None,
        configuration=None: prepare_interview(
            job_description,
            resume_text,
            llm=prep_provider,
            execution_runner=execution_runner,
            configuration=configuration,
        ),
    )
    try:
        yield SimpleNamespace(
            client=TestClient(app),
            revision_store=revision_store,
            prep_provider=prep_provider,
            start_provider=start_provider,
            session_store=session_store,
            draft_store=draft_store,
            regenerator=regenerator,
        )
    finally:
        app.dependency_overrides.clear()


def prep(configured_api, configuration, *, job_description="Backend role"):
    response = configured_api.client.post(
        "/api/prep",
        json={
            "job_description": job_description,
            "resume_text": "Built reliable distributed systems",
            "configuration": configuration.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def start_saved_revision(configured_api, revision_payload):
    prep_calls_before_start = configured_api.prep_provider.calls
    start_calls_before_start = configured_api.start_provider.calls
    request_payload = {
        "plan_revision_id": revision_payload["plan_revision_id"],
        "expected_revision": revision_payload["revision"],
        "plan_sha256": revision_payload["plan_sha256"],
    }
    response = configured_api.client.post("/api/interviews", json=request_payload)

    assert response.status_code == 200, response.text
    assert configured_api.prep_provider.calls == prep_calls_before_start
    assert configured_api.start_provider.calls == start_calls_before_start == 0
    session_id = response.json()["session_id"]
    snapshot_response = configured_api.client.get(f"/api/interviews/{session_id}")
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    saved = configured_api.revision_store.get_by_id(
        revision_payload["plan_revision_id"]
    )

    assert revision_payload["plan_sha256"] == request_payload["plan_sha256"]
    assert snapshot["plan_sha256"] == saved.plan_sha256
    assert snapshot["plan_revision_id"] == saved.plan_revision_id
    assert snapshot["plan_family_id"] == saved.plan_family_id
    assert snapshot["revision"] == saved.revision
    assert snapshot["configuration_snapshot"] == (
        saved.configuration_snapshot.model_dump(mode="json")
    )
    internal_state = configured_api.session_store.get(session_id)
    assert internal_state["plan_snapshot"] == saved.plan.model_dump(mode="json")
    assert snapshot["plan_snapshot"]["schema_version"] == "interview-plan-v2"
    assert snapshot["plan_snapshot"]["configuration_snapshot"] == (
        saved.configuration_snapshot.model_dump(mode="json")
    )
    assert snapshot["plan_snapshot"]["questions"] == (
        public_interview_plan_v2_payload(saved.plan)["questions"]
    )
    public_prep_context = snapshot["plan_snapshot"].get("prep_context")
    if public_prep_context is not None:
        assert "binding_snapshot" not in public_prep_context
    return snapshot


@pytest.mark.parametrize("duration", (15, 30, 45, 60))
@pytest.mark.parametrize(
    "difficulty",
    ("foundation", "intermediate", "advanced"),
)
@pytest.mark.parametrize(
    "focus",
    ("technical_depth", "system_design", "project_review", "balanced"),
)
def test_all_configurations_preserve_revision_and_session_hash_with_zero_start_calls(
    configured_api,
    duration,
    difficulty,
    focus,
):
    configuration = configured_snapshot(
        duration=duration,
        difficulty=difficulty,
        focus=focus,
    )

    preview = prep(configured_api, configuration)
    snapshot = start_saved_revision(configured_api, preview)

    assert len(preview["plan"]["questions"]) == PROFILE_QUESTION_COUNTS[duration]
    assert preview["plan"]["configuration_snapshot"] == (
        configuration.model_dump(mode="json")
    )
    assert snapshot["configuration_snapshot"] == configuration.model_dump(mode="json")


def test_ten_question_plan_and_configured_fallback_both_start_saved_revision(
    configured_api,
):
    configuration = configured_snapshot(
        duration=60,
        difficulty="advanced",
        focus="system_design",
        question_count=10,
    )
    ten_question_preview = prep(configured_api, configuration)
    assert len(ten_question_preview["plan"]["questions"]) == 10
    start_saved_revision(configured_api, ten_question_preview)

    configured_api.prep_provider.omit_last = True
    fallback_preview = prep(
        configured_api,
        configuration,
        job_description="Fallback backend role",
    )

    assert len(fallback_preview["plan"]["questions"]) == 10
    assert fallback_preview["plan"]["title"] == "60 分钟advanced 模拟面试"
    assert all(
        question["origin"] == "generated"
        for question in fallback_preview["plan"]["questions"]
    )
    assert fallback_preview["plan_sha256"] != ten_question_preview["plan_sha256"]
    start_saved_revision(configured_api, fallback_preview)


def test_manual_edit_then_start_uses_latest_edited_hash(configured_api):
    preview = prep(configured_api, configured_snapshot(duration=45))
    first_question_id = preview["plan"]["questions"][0]["question_id"]
    edited = configured_api.client.patch(
        f"/api/interview-plans/{preview['plan_family_id']}",
        json={
            "expected_revision": preview["revision"],
            "request_id": "t53-edit-start",
            "operations": [
                {
                    "op": "edit_question_text",
                    "question_id": first_question_id,
                    "question_text": "Explain a manually edited reliability scenario.",
                }
            ],
        },
    )

    assert edited.status_code == 200
    edited_payload = edited.json()
    assert edited_payload["plan_sha256"] != preview["plan_sha256"]
    assert (
        configured_api.revision_store.get_latest(preview["plan_family_id"]).plan_sha256
        == edited_payload["plan_sha256"]
    )
    snapshot = start_saved_revision(configured_api, edited_payload)
    assert snapshot["plan_sha256"] == edited_payload["plan_sha256"]


def test_reorder_then_start_preserves_ids_positions_and_latest_hash(configured_api):
    preview = prep(configured_api, configured_snapshot(duration=60))
    original_ids = [
        question["question_id"] for question in preview["plan"]["questions"]
    ]
    moved_id = original_ids[0]
    reordered = configured_api.client.patch(
        f"/api/interview-plans/{preview['plan_family_id']}",
        json={
            "expected_revision": preview["revision"],
            "request_id": "t53-reorder-start",
            "operations": [
                {
                    "op": "move_question",
                    "question_id": moved_id,
                    "to_position": len(original_ids),
                }
            ],
        },
    )

    assert reordered.status_code == 200
    reordered_payload = reordered.json()
    reordered_questions = reordered_payload["plan"]["questions"]
    assert reordered_questions[-1]["question_id"] == moved_id
    assert {item["question_id"] for item in reordered_questions} == set(original_ids)
    assert [item["position"] for item in reordered_questions] == list(
        range(1, len(original_ids) + 1)
    )
    snapshot = start_saved_revision(configured_api, reordered_payload)
    assert snapshot["plan_sha256"] == reordered_payload["plan_sha256"]


def test_regenerated_question_then_start_uses_replacement_revision(configured_api):
    preview = prep(configured_api, configured_snapshot(duration=30))
    replaced_id = preview["plan"]["questions"][1]["question_id"]
    regenerated = configured_api.client.post(
        f"/api/interview-plans/{preview['plan_family_id']}/questions/"
        f"{replaced_id}/regenerate",
        json={
            "expected_revision": preview["revision"],
            "request_id": "t53-regenerate-start",
        },
    )

    assert regenerated.status_code == 200
    regenerated_payload = regenerated.json()
    replacement = regenerated_payload["plan"]["questions"][1]
    assert configured_api.regenerator.calls == 1
    assert replacement["question_id"] != replaced_id
    assert replacement["replaces_question_id"] == replaced_id
    assert replacement["origin"] == "regenerated"
    snapshot = start_saved_revision(configured_api, regenerated_payload)
    assert snapshot["plan_sha256"] == regenerated_payload["plan_sha256"]


def test_two_tab_conflict_preserves_winner_and_winner_starts(configured_api):
    preview = prep(configured_api, configured_snapshot(duration=30))
    question_id = preview["plan"]["questions"][0]["question_id"]
    base_operation = {
        "op": "edit_focus",
        "question_id": question_id,
    }
    winner = configured_api.client.patch(
        f"/api/interview-plans/{preview['plan_family_id']}",
        json={
            "expected_revision": preview["revision"],
            "request_id": "t53-tab-a",
            "operations": [{**base_operation, "focus": "tab A winner"}],
        },
    )
    loser = configured_api.client.patch(
        f"/api/interview-plans/{preview['plan_family_id']}",
        json={
            "expected_revision": preview["revision"],
            "request_id": "t53-tab-b",
            "operations": [{**base_operation, "focus": "tab B stale"}],
        },
    )

    assert winner.status_code == 200
    assert loser.status_code == 409
    assert loser.json()["current_revision"] == {
        "plan_revision_id": winner.json()["plan_revision_id"],
        "revision": winner.json()["revision"],
        "plan_sha256": winner.json()["plan_sha256"],
    }
    assert len(
        configured_api.revision_store.list_revisions(preview["plan_family_id"])
    ) == 2
    snapshot = start_saved_revision(configured_api, winner.json())
    assert snapshot["plan_sha256"] == winner.json()["plan_sha256"]


def test_changed_job_description_marks_saved_plan_stale(configured_api):
    preview = prep(
        configured_api,
        configured_snapshot(duration=30),
        job_description="Original backend role",
    )
    created = configured_api.client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Original backend role",
            "resume_text": "Built reliable distributed systems",
            "plan_family_id": preview["plan_family_id"],
            "latest_plan_revision_id": preview["plan_revision_id"],
        },
    )
    changed = configured_api.client.post(
        "/api/interview-drafts",
        json={
            "draft_id": created.json()["draft_id"],
            "job_description": "Changed platform role",
            "resume_text": "Built reliable distributed systems",
        },
    )

    assert created.status_code == 200
    assert created.json()["plan_status"] == "active"
    assert changed.status_code == 200
    assert changed.json()["plan_status"] == "stale"
    assert changed.json()["latest_plan_revision_id"] == preview["plan_revision_id"]
    assert changed.json()["plan_family_id"] == preview["plan_family_id"]


def test_provider_unavailable_after_prep_cannot_interrupt_saved_plan_start(
    configured_api,
):
    preview = prep(configured_api, configured_snapshot(duration=60))
    prep_calls = configured_api.prep_provider.calls
    configured_api.prep_provider.available = False
    configured_api.regenerator.available = False

    snapshot = start_saved_revision(configured_api, preview)

    assert configured_api.prep_provider.calls == prep_calls
    assert configured_api.regenerator.calls == 0
    assert configured_api.start_provider.calls == 0
    assert snapshot["plan_sha256"] == preview["plan_sha256"]
