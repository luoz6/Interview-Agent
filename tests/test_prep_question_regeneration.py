from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import routes as route_module
from app.main import app
from app.services.in_memory_interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.prep_plans import PrepPlanError
from app.services.prep_question_regeneration import PrepQuestionRegenerator
from app.services.runtime import get_prep_plan_store
from app.services.session import InterviewSessionStore


def plan_with_context(*, replacement: bool = False) -> InterviewPlan:
    prompts = (
        [
            "How would you keep cache and database writes consistent under retries?",
            "Describe a queue backpressure strategy for burst traffic.",
            "Design an idempotent payment callback boundary.",
            "Explain how you would diagnose a PostgreSQL lock incident.",
        ]
        if replacement
        else [
            "Describe one cache consistency decision from your project.",
            "Explain one concurrency failure you handled.",
            "Design a service for ten times the current traffic.",
            "Describe a difficult technical trade-off with your team.",
        ]
    )
    focuses = [
        "缓存一致性",
        "并发控制",
        "系统设计",
        "技术决策",
    ]
    questions = [
        InterviewQuestion(
            id=f"q{index}",
            kind=("technical", "technical", "system-design", "behavioral")[
                index - 1
            ],
            prompt=prompts[index - 1],
            focus=focuses[index - 1],
        )
        for index in range(1, 5)
    ]
    evidence_id = "knowledge-cache-v2" if replacement else "knowledge-cache-v1"
    topic_id = "topic-cache-v2" if replacement else "topic-cache-v1"
    return InterviewPlan(
        title="Editable authoritative plan",
        questions=questions,
        prep_context=PrepContext(
            summary="Knowledge context is available.",
            knowledge_status="completed",
            topics=[
                PrepKnowledgeTopic(
                    id=topic_id,
                    label="缓存一致性",
                    source="retrieval",
                    evidence="Safe topic summary",
                    evidence_ids=[evidence_id],
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=[topic_id],
                    follow_up_hints=["追问一致性窗口和失败恢复。"],
                    evidence_titles=["Cache consistency note"],
                    evidence_ids=[evidence_id],
                ),
                *[
                    PrepQuestionHint(question_id=f"q{index}")
                    for index in range(2, 5)
                ],
            ],
        ),
    )


def create_plan(store: InMemoryPrepPlanStore) -> dict:
    return store.create(
        plan=plan_with_context(),
        job_description="Backend role with Redis and PostgreSQL",
        resume_text="Built a cache-backed order platform",
        job_tags=["redis", "postgresql"],
    )


def test_public_questions_include_safe_topic_and_evidence_metadata():
    public = create_plan(InMemoryPrepPlanStore())
    first = public["questions"][0]

    assert first["source_signals"] == ["jd", "resume", "knowledge"]
    assert first["topic_labels"] == ["缓存一致性"]
    assert first["evidence_ids"] == ["knowledge-cache-v1"]
    assert public["questions"][1]["evidence_ids"] == []


def test_regeneration_replaces_one_question_and_projects_updated_context():
    store = InMemoryPrepPlanStore()
    public = create_plan(store)
    target_id = public["questions"][0]["question_id"]
    required = store.apply_operations(
        public["plan_id"],
        expected_version=1,
        operations=[
            {"type": "set_required", "question_id": target_id, "required": True}
        ],
    )
    regenerator = PrepQuestionRegenerator(lambda _context: plan_with_context(replacement=True))

    regenerated = regenerator.regenerate(
        store,
        plan_id=public["plan_id"],
        question_id=target_id,
        expected_version=required["plan_version"],
    )

    replacement_id = regenerated["replacement_question_id"]
    assert regenerated["replaced_question_id"] == target_id
    assert UUID(replacement_id.removeprefix("pq_")).version == 4
    assert regenerated["plan_version"] == 3
    assert store.version_count(public["plan_id"]) == 3
    replacement = regenerated["questions"][0]
    assert replacement["question_id"] == replacement_id
    assert replacement["position"] == 1
    assert replacement["required"] is True
    assert replacement["evidence_ids"] == ["knowledge-cache-v2"]

    sessions = InterviewSessionStore()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=store,
        session_store=sessions,
        launch_repository=InMemoryInterviewLaunchRepository(),
    )
    launched = coordinator.launch(
        plan_id=public["plan_id"],
        expected_plan_version=3,
        command_id=f"start_{uuid4()}",
    )
    launched_plan = sessions.get(launched["session_id"])["plan"]
    assert launched_plan.questions[0].prompt == replacement["prompt"]
    assert launched_plan.prep_context.question_hints[0].question_id == "q1"
    assert launched_plan.prep_context.question_hints[0].evidence_ids == [
        "knowledge-cache-v2"
    ]
    assert [item.id for item in launched_plan.prep_context.topics] == [
        "topic-cache-v2"
    ]
    assert all(
        item.evidence_id != "knowledge-cache-v1"
        for item in launched_plan.prep_context.evidence_refs
    )


def test_regeneration_failure_and_duplicate_leave_version_history_unchanged():
    store = InMemoryPrepPlanStore()
    public = create_plan(store)
    target_id = public["questions"][0]["question_id"]

    def fail(_context):
        raise RuntimeError("provider secret that must not escape")

    with pytest.raises(PrepPlanError) as failed:
        PrepQuestionRegenerator(fail).regenerate(
            store,
            plan_id=public["plan_id"],
            question_id=target_id,
            expected_version=1,
        )
    assert failed.value.code == "PREP_PLAN_REGENERATION_FAILED"
    assert "provider secret" not in failed.value.message
    assert store.get(public["plan_id"])["plan_version"] == 1
    assert store.version_count(public["plan_id"]) == 1

    with pytest.raises(PrepPlanError) as duplicate:
        PrepQuestionRegenerator(lambda _context: plan_with_context()).regenerate(
            store,
            plan_id=public["plan_id"],
            question_id=target_id,
            expected_version=1,
        )
    assert duplicate.value.code == "PREP_PLAN_REGENERATION_DUPLICATE"
    assert store.get(public["plan_id"])["plan_version"] == 1
    assert store.version_count(public["plan_id"]) == 1


def test_regeneration_cas_does_not_overwrite_a_concurrent_patch():
    store = InMemoryPrepPlanStore()
    public = create_plan(store)
    target_id = public["questions"][0]["question_id"]
    other_id = public["questions"][1]["question_id"]

    def generate_after_concurrent_patch(_context):
        store.apply_operations(
            public["plan_id"],
            expected_version=1,
            operations=[
                {"type": "set_focus", "question_id": other_id, "focus": "事务边界"}
            ],
        )
        return plan_with_context(replacement=True)

    with pytest.raises(PrepPlanError) as conflict:
        PrepQuestionRegenerator(generate_after_concurrent_patch).regenerate(
            store,
            plan_id=public["plan_id"],
            question_id=target_id,
            expected_version=1,
        )
    assert conflict.value.code == "PREP_PLAN_VERSION_CONFLICT"
    current = store.get(public["plan_id"])
    assert current["plan_version"] == 2
    assert current["questions"][0]["question_id"] == target_id
    assert current["questions"][1]["focus"] == "事务边界"
    assert store.version_count(public["plan_id"]) == 2


def test_regeneration_api_returns_stable_replacement_ids():
    store = InMemoryPrepPlanStore()
    public = create_plan(store)
    target_id = public["questions"][0]["question_id"]
    regenerator = PrepQuestionRegenerator(lambda _context: plan_with_context(replacement=True))
    app.dependency_overrides[get_prep_plan_store] = lambda: store
    app.dependency_overrides[route_module.get_prep_question_regenerator] = lambda: regenerator
    try:
        response = TestClient(app).post(
            f"/api/prep-plans/{public['plan_id']}/questions/{target_id}/regenerate",
            json={"expected_version": 1},
        )
    finally:
        app.dependency_overrides.pop(get_prep_plan_store, None)
        app.dependency_overrides.pop(route_module.get_prep_question_regenerator, None)

    assert response.status_code == 200
    body = response.json()
    assert body["replaced_question_id"] == target_id
    assert body["replacement_question_id"] != target_id
    assert body["plan_version"] == 2
