import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.shared.projections import plan_revision_payload
from app.domain.interview.question_intent import QuestionIntentV1
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    InterviewPlanV3,
    PlanConfigurationSnapshot,
    PlanSourcePayload,
    legacy_plan_to_v3,
    plan_payload_sha256,
    v2_plan_to_legacy,
    v2_plan_to_v3,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.interview_plan_editor import (
    InterviewPlanEditor,
    PlanEditRequest,
    PlanOperation,
    PlanOperationValidationError,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session_plan_binding import (
    SessionPlanBinding,
    session_plan_binding_from_revision,
)


def _configuration(question_count: int = 2) -> PlanConfigurationSnapshot:
    return PlanConfigurationSnapshot(
        difficulty="intermediate",
        target_duration_minutes=30,
        focus_preset="balanced",
        question_type_budget={"project": 1, "technical": question_count - 1},
        expected_followup_budget=question_count,
        generator_version="plan-generator-v3-test",
        followup_policy_version="adaptive_v1",
    )


def _v2_plan() -> InterviewPlanV2:
    configuration = _configuration()
    return InterviewPlanV2(
        title="后端工程师真人面试",
        configuration_snapshot=configuration,
        questions=(
            InterviewPlanQuestionV2(
                question_id=str(uuid4()),
                position=1,
                question_text="你主导的库存扣减方案出现 MQ 投递失败时如何恢复？",
                focus="库存扣减与最终一致性",
                question_type="project",
                difficulty="intermediate",
                expected_minutes=8,
                expected_followups=1,
                origin="generated",
            ),
            InterviewPlanQuestionV2(
                question_id=str(uuid4()),
                position=2,
                question_text="请解释幂等重试的实现细节和权衡。",
                focus="幂等重试",
                question_type="technical",
                difficulty="intermediate",
                expected_minutes=8,
                expected_followups=1,
                origin="edited",
            ),
        ),
    )


def test_question_intent_is_strict_and_contains_no_final_question_field():
    intent = QuestionIntentV1(
        question_id="q1",
        position=1,
        kind="project",
        focus="库存扣减与最终一致性",
        difficulty="advanced",
        assessment_goals=("failure_mode", "recovery", "tradeoff"),
        expected_minutes=8,
        expected_followups=1,
        knowledge_binding={},
    )

    payload = intent.model_dump(mode="json")
    assert payload["knowledge_binding"] == {}
    assert "prompt" not in payload
    assert "question_text" not in payload

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        QuestionIntentV1(**payload, question_text="不得进入 Intent")
    with pytest.raises(ValidationError, match="must be an integer"):
        QuestionIntentV1(**{**payload, "position": True})
    with pytest.raises(ValidationError, match="assessment_goals"):
        QuestionIntentV1(**{**payload, "assessment_goals": []})


def test_v2_to_v3_is_deterministic_intent_only_and_preserves_plan_shape():
    source = _v2_plan()

    first = v2_plan_to_v3(source)
    second = v2_plan_to_v3(source)

    assert isinstance(first, InterviewPlanV3)
    assert first == second
    assert plan_payload_sha256(first) == plan_payload_sha256(second)
    assert [item.question_id for item in first.questions] == [
        item.question_id for item in source.questions
    ]
    assert [item.position for item in first.questions] == [1, 2]
    assert [item.kind for item in first.questions] == ["project", "technical"]
    assert first.questions[1].origin == "custom"
    assert "recovery" in first.questions[0].assessment_goals
    assert "reliability" in first.questions[1].assessment_goals
    serialized = json.dumps(first.model_dump(mode="json"), ensure_ascii=False)
    assert "question_text" not in serialized
    assert "你主导的库存扣减方案" not in serialized


def test_v3_drops_legacy_question_text_from_prep_context():
    source = _v2_plan().model_copy(
        update={
            "prep_context": {
                "schema_version": "v2",
                "question_hints": [
                    {"question_id": "old", "question_text": "旧的最终问句"}
                ],
                "questions": [{"prompt": "也不能持久化"}],
            }
        }
    )
    converted = v2_plan_to_v3(source)
    serialized = json.dumps(converted.model_dump(mode="json"), ensure_ascii=False)
    assert "question_text" not in serialized
    assert "旧的最终问句" not in serialized
    assert "不能持久化" not in serialized


def test_v3_cannot_be_silently_projected_to_legacy_final_question_plan():
    with pytest.raises(ValueError, match="v3 intent plans"):
        v2_plan_to_legacy(v2_plan_to_v3(_v2_plan()))


def test_legacy_to_v3_uses_stable_ids_and_hashes_without_mutating_legacy():
    legacy = InterviewPlan(
        title="Legacy plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="How did you recover after an MQ delivery failure?",
                focus="MQ recovery",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain idempotent retries.",
                focus="idempotent retry",
            ),
        ],
    )
    configuration = _configuration()

    first = legacy_plan_to_v3(legacy, configuration_snapshot=configuration)
    second = legacy_plan_to_v3(legacy, configuration_snapshot=configuration)

    assert first == second
    assert plan_payload_sha256(first) == plan_payload_sha256(second)
    assert legacy.questions[0].prompt.startswith("How did")
    assert all(item.question_id not in {"q1", "q2"} for item in first.questions)
    assert len({item.question_id for item in first.questions}) == 2


def test_v3_conversion_is_a_new_revision_and_session_binding_replays_both_versions():
    store = InMemoryInterviewPlanRevisionStore()
    source_plan = _v2_plan()
    initial = store.create_initial(
        source_payload=PlanSourcePayload(
            job_description="负责高可靠后端服务。",
            resume_text="实现过库存与消息一致性方案。",
        ),
        plan=source_plan,
        retention_policy="local-v1",
        generator_version=source_plan.configuration_snapshot.generator_version,
    )
    candidate = v2_plan_to_v3(initial.plan)
    converted = store.create_next_revision(
        plan_family_id=initial.plan_family_id,
        expected_revision=1,
        plan=candidate,
        source_kind="customized",
        created_reason="convert_to_intent_plan",
        generator_version=candidate.configuration_snapshot.generator_version,
    )

    assert initial.plan.schema_version == "interview-plan-v2"
    assert store.get_by_id(initial.plan_revision_id).plan == source_plan
    assert converted.plan.schema_version == "interview-plan-v3"
    assert converted.parent_revision_id == initial.plan_revision_id
    assert converted.plan_sha256 != initial.plan_sha256

    v2_binding = session_plan_binding_from_revision(initial)
    v3_binding = session_plan_binding_from_revision(converted)
    assert SessionPlanBinding.model_validate(
        v2_binding.model_dump(mode="json")
    ).plan_snapshot["schema_version"] == "interview-plan-v2"
    assert SessionPlanBinding.model_validate(
        v3_binding.model_dump(mode="json")
    ).plan_snapshot["schema_version"] == "interview-plan-v3"

    tampered = v3_binding.model_dump(mode="json")
    tampered["plan_snapshot"]["schema_version"] = "interview-plan-v2"
    with pytest.raises(ValidationError):
        SessionPlanBinding.model_validate(tampered)


def test_v3_revision_public_projection_does_not_invent_legacy_questions():
    _, revision = _v3_revision_store()

    payload = plan_revision_payload(revision)

    assert payload["legacy_plan"] is None
    assert payload["plan"]["schema_version"] == "interview-plan-v3"
    assert payload["plan"]["questions"][1]["focus"] == "幂等重试"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "question_text" not in serialized
    assert "请解释幂等重试" not in serialized


def _v3_revision_store():
    store = InMemoryInterviewPlanRevisionStore()
    plan = v2_plan_to_v3(_v2_plan())
    revision = store.create_initial(
        source_payload=PlanSourcePayload(
            job_description="负责高可靠后端服务。",
            resume_text="实现过库存与消息一致性方案。",
        ),
        plan=plan,
        retention_policy="local-v1",
        generator_version=plan.configuration_snapshot.generator_version,
    )
    return store, revision


def test_v3_editor_updates_intent_without_creating_final_question_text():
    store, revision = _v3_revision_store()
    question = revision.plan.questions[0]

    updated = InterviewPlanEditor(store).apply(
        revision.plan_family_id,
        PlanEditRequest(
            expected_revision=1,
            request_id="edit-v3-intent",
            operations=(
                PlanOperation(
                    op="edit_focus",
                    question_id=question.question_id,
                    focus="库存扣减、消息失败与补偿恢复",
                ),
                PlanOperation(
                    op="edit_assessment_goals",
                    question_id=question.question_id,
                    assessment_goals=("failure_mode", "recovery"),
                ),
            ),
        ),
    )

    payload = updated.plan.model_dump(mode="json")
    assert updated.revision == 2
    assert payload["questions"][0]["focus"] == "库存扣减、消息失败与补偿恢复"
    assert payload["questions"][0]["assessment_goals"] == [
        "failure_mode",
        "recovery",
    ]
    assert "question_text" not in json.dumps(payload, ensure_ascii=False)


def test_v3_editor_rejects_final_question_text_edits():
    store, revision = _v3_revision_store()

    with pytest.raises(PlanOperationValidationError, match="assessment intent"):
        InterviewPlanEditor(store).apply(
            revision.plan_family_id,
            PlanEditRequest(
                expected_revision=1,
                request_id="illegal-v3-question-text",
                operations=(
                    PlanOperation(
                        op="edit_question_text",
                        question_id=revision.plan.questions[0].question_id,
                        question_text="不允许保存的最终问句？",
                    ),
                ),
            ),
        )


def test_v3_editor_adds_custom_intent_with_unbound_knowledge():
    store, revision = _v3_revision_store()

    updated = InterviewPlanEditor(store).apply(
        revision.plan_family_id,
        PlanEditRequest(
            expected_revision=1,
            request_id="add-v3-intent",
            operations=(
                PlanOperation(
                    op="add_custom_intent",
                    focus="RocketMQ 事务消息的故障边界",
                    kind="technical",
                    difficulty="advanced",
                    assessment_goals=("failure_mode", "tradeoff"),
                    expected_minutes=6,
                    expected_followups=1,
                ),
            ),
        ),
    )

    added = updated.plan.questions[-1]
    assert added.origin == "custom"
    assert added.knowledge_binding["status"] == "unbound"
    assert added.position == len(updated.plan.questions)
