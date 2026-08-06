from uuid import uuid4

import pytest

from app.services.interview_plan_editor import (
    InterviewPlanEditor,
    PlanEditRequest,
    PlanOperationValidationError,
)
from app.services.interview_plan_revision import InterviewPlanQuestionV2
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
    PlanRevisionConflict,
)
from tests.test_interview_plan_revision import plan, source


def setup_editor():
    store = InMemoryInterviewPlanRevisionStore()
    initial = store.create_initial(
        source_payload=source(),
        plan=plan(),
        retention_policy="local-v1",
        generator_version="plan-generator-v2-test",
    )
    return store, InterviewPlanEditor(store), initial


def request(operation, *, revision=1, request_id=None):
    return PlanEditRequest(
        expected_revision=revision,
        request_id=request_id or f"request-{uuid4()}",
        operations=[operation],
    )


def test_edit_preserves_question_id_and_move_only_changes_positions():
    store, editor, initial = setup_editor()
    question_id = initial.plan.questions[0].question_id
    edited = editor.apply(
        initial.plan_family_id,
        request(
            {"op": "edit_question_text", "question_id": question_id, "question_text": "新的问题文本"}
        ),
    )
    moved = editor.apply(
        initial.plan_family_id,
        request(
            {"op": "move_question", "question_id": question_id, "to_position": 3},
            revision=2,
        ),
    )

    assert edited.plan.questions[0].question_id == question_id
    assert edited.plan.questions[0].origin == "edited"
    assert moved.plan.questions[2].question_id == question_id
    assert [item.position for item in moved.plan.questions] == [1, 2, 3]
    assert [item.revision for item in store.list_revisions(initial.plan_family_id)] == [1, 2, 3]


def test_regenerate_replaces_identity_and_records_lineage():
    _, editor, initial = setup_editor()
    old = initial.plan.questions[1]
    result = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "regenerate_question",
                "question_id": old.question_id,
                "question_text": "请分析缓存雪崩的治理方案。",
                "focus": "缓存稳定性",
                "question_type": "technical",
                "difficulty": "intermediate",
                "expected_minutes": 8,
                "expected_followups": 1,
            }
        ),
    )

    replacement = result.plan.questions[1]
    assert replacement.question_id != old.question_id
    assert replacement.replaces_question_id == old.question_id
    assert replacement.origin == "regenerated"


def test_restore_appends_a_new_monotonic_revision_instead_of_moving_pointer():
    store, editor, initial = setup_editor()
    changed = editor.apply(
        initial.plan_family_id,
        request(
            {"op": "edit_focus", "question_id": initial.plan.questions[0].question_id, "focus": "changed"}
        ),
    )
    restored = editor.apply(
        initial.plan_family_id,
        request(
            {"op": "restore_revision", "target_revision_id": initial.plan_revision_id},
            revision=2,
        ),
    )

    assert changed.revision == 2
    assert restored.revision == 3
    assert restored.plan == initial.plan
    assert restored.parent_revision_id == changed.plan_revision_id
    assert len(store.list_revisions(initial.plan_family_id)) == 3


def test_duplicate_request_id_returns_same_revision_and_conflicting_payload_fails():
    _, editor, initial = setup_editor()
    operation = {
        "op": "edit_focus",
        "question_id": initial.plan.questions[0].question_id,
        "focus": "idempotent focus",
    }
    first_request = request(operation, request_id="same-request")
    first = editor.apply(initial.plan_family_id, first_request)
    replay = editor.apply(initial.plan_family_id, first_request)

    assert replay.plan_revision_id == first.plan_revision_id
    with pytest.raises(PlanRevisionConflict, match="payload conflicts"):
        editor.apply(
            initial.plan_family_id,
            request(
                operation | {"focus": "different payload"},
                revision=1,
                request_id="same-request",
            ),
        )


def test_minimum_maximum_duplicate_and_position_constraints_are_structured():
    _, editor, initial = setup_editor()
    two_questions = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "delete_question",
                "question_id": initial.plan.questions[0].question_id,
            }
        ),
    )
    one_question = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "delete_question",
                "question_id": two_questions.plan.questions[0].question_id,
            },
            revision=2,
        ),
    )
    with pytest.raises(PlanOperationValidationError) as minimum:
        editor.apply(
            initial.plan_family_id,
            request(
                {
                    "op": "delete_question",
                    "question_id": one_question.plan.questions[0].question_id,
                },
                revision=3,
            ),
        )
    assert minimum.value.detail()["code"] == "minimum_question_count"

    _, editor, initial = setup_editor()
    with pytest.raises(PlanOperationValidationError) as duplicate:
        editor.apply(
            initial.plan_family_id,
            request(
                {
                    "op": "edit_question_text",
                    "question_id": initial.plan.questions[1].question_id,
                    "question_text": initial.plan.questions[0].question_text,
                }
            ),
        )
    assert duplicate.value.detail()["code"] == "duplicate_question"

    with pytest.raises(PlanOperationValidationError) as position:
        editor.apply(
            initial.plan_family_id,
            request(
                {"op": "move_question", "question_id": initial.plan.questions[0].question_id, "to_position": 4}
            ),
        )
    assert position.value.detail()["code"] == "position_out_of_range"


def test_add_custom_question_is_server_identified_and_respects_maximum():
    _, editor, initial = setup_editor()
    add = {
        "op": "add_custom_question",
        "question_text": "请说明一次技术复盘。",
        "focus": "复盘能力",
        "question_type": "behavioral",
        "difficulty": "intermediate",
        "expected_minutes": 6,
        "expected_followups": 1,
    }
    revisions = []
    current_revision = 1
    for question_number in range(4, 11):
        created = editor.apply(
            initial.plan_family_id,
            request(
                add
                | {
                    "question_text": f"请说明第 {question_number} 个不同场景。",
                },
                revision=current_revision,
            ),
        )
        revisions.append(created)
        current_revision += 1
    with pytest.raises(PlanOperationValidationError) as maximum:
        editor.apply(
            initial.plan_family_id,
            request(
                add | {"question_text": "第十一题"},
                revision=current_revision,
            ),
        )

    assert all(item.plan.questions[-1].origin == "custom" for item in revisions)
    assert len({item.plan.questions[-1].question_id for item in revisions}) == 7
    assert len(revisions[-1].plan.questions) == 10
    assert maximum.value.code == "maximum_question_count"
