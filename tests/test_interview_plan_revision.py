from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    PlanSourcePayload,
    canonical_sha256,
    plan_payload_sha256,
    source_payload_sha256,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
    PlanRevisionConflict,
    PlanSourceInUse,
    PlanSourceUnavailable,
)


def configuration() -> PlanConfigurationSnapshot:
    return PlanConfigurationSnapshot(
        difficulty="intermediate",
        target_duration_minutes=30,
        focus_preset="balanced",
        question_type_budget={"technical": 1, "project": 1, "system-design": 1},
        expected_followup_budget=3,
        generator_version="plan-generator-v2-test",
        followup_policy_version="fixed_v1",
    )


def plan(*, title: str = "后端工程师模拟面试", suffix: str = "") -> InterviewPlanV2:
    config = configuration()
    kinds = ("project", "technical", "system-design")
    return InterviewPlanV2(
        title=title,
        configuration_snapshot=config,
        questions=[
            InterviewPlanQuestionV2(
                question_id=str(uuid4()),
                position=index,
                question_text=f"问题 {index}：请说明方案与取舍。{suffix}",
                focus=f"重点 {index}",
                question_type=kind,
                difficulty="intermediate",
                expected_minutes=8,
                expected_followups=1,
                origin="generated",
            )
            for index, kind in enumerate(kinds, start=1)
        ],
    )


def source() -> PlanSourcePayload:
    return PlanSourcePayload(
        job_description="负责 Redis 与 PostgreSQL 服务。\r\n强调稳定性。",
        resume_text="合成简历：实现过缓存一致性方案。",
        job_tags=["Redis", "PostgreSQL", "Redis"],
    )


def test_canonical_hash_is_stable_for_key_order_unicode_and_line_endings():
    left = {"标题": "缓存，一致性", "配置": {"b": 2, "a": "e\u0301\r\n下一行"}}
    right = {"配置": {"a": "é\n下一行", "b": 2}, "标题": "缓存，一致性"}

    assert canonical_sha256(left) == canonical_sha256(right)
    assert len(source_payload_sha256(source())) == 64


def test_v2_plan_uses_stable_opaque_ids_and_rejects_legacy_q_sequence_contract():
    item = plan()

    assert item.schema_version == "interview-plan-v2"
    assert [question.position for question in item.questions] == [1, 2, 3]
    assert all(not question.question_id.startswith("q") for question in item.questions)

    payload = item.model_dump(mode="json")
    payload["questions"][1]["question_id"] = payload["questions"][0]["question_id"]
    with pytest.raises(ValidationError, match="question_id must be unique"):
        InterviewPlanV2.model_validate(payload)


def test_initial_and_next_revision_share_one_source_without_raw_duplication():
    store = InMemoryInterviewPlanRevisionStore()
    first_plan = plan()
    first = store.create_initial(
        source_payload=source(),
        plan=first_plan,
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    second_plan = first_plan.model_copy(update={"title": "编辑后的计划"})
    second = store.create_next_revision(
        plan_family_id=first.plan_family_id,
        expected_revision=1,
        plan=second_plan,
        source_kind="edited",
        created_reason="edit_focus",
        generator_version="plan-generator-v2-test",
    )

    assert second.revision == 2
    assert second.parent_revision_id == first.plan_revision_id
    assert second.source_id == first.source_id
    assert second.source_sha256 == first.source_sha256
    assert store.get_source(first.source_id).protected_payload == source()
    assert [item.plan_sha256 for item in store.list_revisions(first.plan_family_id)] == [
        plan_payload_sha256(first_plan),
        plan_payload_sha256(second_plan),
    ]


def test_saved_revision_cannot_be_mutated_in_place_or_through_a_returned_copy():
    store = InMemoryInterviewPlanRevisionStore()
    saved = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )

    with pytest.raises(ValidationError, match="frozen"):
        saved.revision = 99
    saved.plan.questions[0].knowledge_binding["attempted_mutation"] = True
    reloaded = store.get_by_id(saved.plan_revision_id)
    assert "attempted_mutation" not in reloaded.plan.questions[0].knowledge_binding
    assert reloaded.plan_sha256 == plan_payload_sha256(reloaded.plan)


def test_two_concurrent_expected_revision_writes_only_allow_one_success():
    store = InMemoryInterviewPlanRevisionStore()
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )

    def write(suffix: str):
        try:
            return store.create_next_revision(
                plan_family_id=first.plan_family_id,
                expected_revision=1,
                plan=plan(title=f"concurrent-{suffix}"),
                source_kind="edited",
                created_reason="edit_question_text",
                generator_version="plan-generator-v2-test",
            )
        except PlanRevisionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ("a", "b")))

    assert sum(hasattr(item, "plan_revision_id") for item in results) == 1
    conflicts = [item for item in results if isinstance(item, PlanRevisionConflict)]
    assert len(conflicts) == 1
    assert conflicts[0].current_revision == 2
    assert store.get_latest(first.plan_family_id).revision == 2


def test_source_references_require_explicit_release_before_tombstone():
    store = InMemoryInterviewPlanRevisionStore()
    first = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    store.add_source_reference(first.source_id, owner_type="draft", owner_id="draft-1")
    store.add_source_reference(first.source_id, owner_type="session", owner_id="session-1")

    with pytest.raises(PlanSourceInUse):
        store.tombstone_source_payload(first.source_id, reason="retention_expired")
    assert store.remove_source_reference(
        first.source_id, owner_type="draft", owner_id="draft-1"
    )
    assert store.remove_source_reference(
        first.source_id, owner_type="session", owner_id="session-1"
    )
    assert store.remove_source_reference(
        first.source_id, owner_type="family", owner_id=first.plan_family_id
    )
    tombstone = store.tombstone_source_payload(
        first.source_id, reason="retention_expired"
    )

    assert tombstone.protected_payload is None
    assert tombstone.tombstoned_at is not None
    assert store.get_by_id(first.plan_revision_id).plan == first.plan

    with pytest.raises(PlanSourceUnavailable):
        store.create_next_revision(
            plan_family_id=first.plan_family_id,
            expected_revision=1,
            plan=plan(),
            source_kind="regenerated_question",
            created_reason="regenerate_question",
            generator_version="plan-generator-v2-test",
        )

    edited = store.create_next_revision(
        plan_family_id=first.plan_family_id,
        expected_revision=1,
        plan=first.plan.model_copy(update={"title": "source-free edit"}),
        source_kind="edited",
        created_reason="edit_question_text",
        generator_version="plan-generator-v2-test",
    )
    assert edited.revision == 2

    with pytest.raises(PlanSourceUnavailable):
        store.add_source_reference(
            first.source_id,
            owner_type="session",
            owner_id="late-session",
        )


def test_memory_reconcile_validates_all_expected_sources_before_mutating_refs():
    store = InMemoryInterviewPlanRevisionStore()
    current = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    unavailable = store.create_initial(
        source_payload=source(),
        plan=plan(title="Unavailable target"),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    store.add_source_reference(
        current.source_id, owner_type="draft", owner_id="draft-atomic"
    )
    store.remove_source_reference(
        unavailable.source_id,
        owner_type="family",
        owner_id=unavailable.plan_family_id,
    )
    store.tombstone_source_payload(
        unavailable.source_id, reason="retention_expired"
    )
    before = store.list_source_references(current.source_id)

    with pytest.raises(PlanSourceUnavailable):
        store.reconcile_source_references(
            owner_type="draft",
            expected={"draft-atomic": unavailable.source_id},
        )

    assert store.list_source_references(current.source_id) == before
