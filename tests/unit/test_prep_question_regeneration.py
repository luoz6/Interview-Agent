from uuid import UUID, uuid4

import pytest
from app.services.in_memory_interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.prep_plans import PrepPlanError
from app.services.prep_question_regeneration import PrepQuestionRegenerator
from app.services.session import InterviewSessionStore
from tests.interview_fixtures import (
    create_in_memory_prep_plan,
    interview_plan_with_context,
)


def test_public_questions_include_safe_topic_and_evidence_metadata():
    public = create_in_memory_prep_plan(InMemoryPrepPlanStore())
    first = public["questions"][0]

    assert first["source_signals"] == ["jd", "resume", "knowledge"]
    assert first["topic_labels"] == ["缓存一致性"]
    assert first["evidence_ids"] == ["knowledge-cache-v1"]
    assert public["questions"][1]["evidence_ids"] == []


def test_regeneration_replaces_one_question_and_projects_updated_context():
    store = InMemoryPrepPlanStore()
    public = create_in_memory_prep_plan(store)
    target_id = public["questions"][0]["question_id"]
    required = store.apply_operations(
        public["plan_id"],
        expected_version=1,
        operations=[
            {"type": "set_required", "question_id": target_id, "required": True}
        ],
    )
    regenerator = PrepQuestionRegenerator(
        lambda _context: interview_plan_with_context(replacement=True)
    )

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
    public = create_in_memory_prep_plan(store)
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
        PrepQuestionRegenerator(
            lambda _context: interview_plan_with_context()
        ).regenerate(
            store,
            plan_id=public["plan_id"],
            question_id=target_id,
            expected_version=1,
        )
    assert duplicate.value.code == "PREP_PLAN_REGENERATION_DUPLICATE"
    assert store.get(public["plan_id"])["plan_version"] == 1
    assert store.version_count(public["plan_id"]) == 1


def test_regeneration_quality_compares_replacement_with_all_remaining_questions():
    store = InMemoryPrepPlanStore()
    public = create_in_memory_prep_plan(store)
    target_id = public["questions"][0]["question_id"]
    remaining_prompt = public["questions"][1]["prompt"]
    generated = interview_plan_with_context(replacement=True)
    generated.questions[0].prompt = "".join(
        chr(ord(character) + 0xFEE0)
        if "!" <= character <= "~"
        else character
        for character in remaining_prompt
    )

    with pytest.raises(PrepPlanError) as rejected:
        PrepQuestionRegenerator(lambda _context: generated).regenerate(
            store,
            plan_id=public["plan_id"],
            question_id=target_id,
            expected_version=1,
        )

    assert rejected.value.code == "PREP_PLAN_REGENERATION_QUALITY_VIOLATION"
    assert rejected.value.details == {
        "question_id": target_id,
        "quality_code": "near_duplicate_question",
    }
    assert remaining_prompt not in rejected.value.message
    assert store.get(public["plan_id"])["plan_version"] == 1
    assert store.version_count(public["plan_id"]) == 1


def test_regeneration_cas_does_not_overwrite_a_concurrent_patch():
    store = InMemoryPrepPlanStore()
    public = create_in_memory_prep_plan(store)
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
        return interview_plan_with_context(replacement=True)

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
