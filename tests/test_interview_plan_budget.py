from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import app.services.interview_plan_budget as budget_module
from app.services.interview_plan_budget import (
    INTERVIEW_PLAN_BUDGET_CANONICAL_SHA256,
    INTERVIEW_PLAN_BUDGET_VERSION,
    INTERVIEW_PLAN_ESTIMATE_FORMULA_VERSION,
    PlanBudgetAssessment,
    allocate_expected_followups,
    allocate_main_answer_minutes,
    assess_interview_plan_budget,
    duration_budget_policy_sha256,
    duration_budget_profile,
    estimate_plan_duration,
    validate_duration_budget_policy,
)
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    legacy_plan_to_v2,
)
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    fallback_interview_plan,
    validate_launchable_interview_plan,
)


def configuration(
    *,
    duration: int = 30,
    question_type_budget: dict[str, int] | None = None,
    expected_followup_budget: int | None = None,
    difficulty: str = "intermediate",
    focus: str = "balanced",
) -> PlanConfigurationSnapshot:
    type_budget = question_type_budget or {
        "project": 2,
        "technical": 2,
        "system-design": 1,
    }
    return PlanConfigurationSnapshot(
        difficulty=difficulty,
        target_duration_minutes=duration,
        focus_preset=focus,
        question_type_budget=type_budget,
        expected_followup_budget=(
            sum(type_budget.values())
            if expected_followup_budget is None
            else expected_followup_budget
        ),
        generator_version="plan-generator-v2-budget-test",
        followup_policy_version="adaptive_v1",
    )


def question(
    position: int,
    *,
    question_type: str = "technical",
    expected_minutes: int = 4,
    expected_followups: int = 1,
) -> InterviewPlanQuestionV2:
    return InterviewPlanQuestionV2(
        question_id=str(uuid4()),
        position=position,
        question_text=f"Question {position}",
        focus=f"Focus {position}",
        question_type=question_type,
        difficulty="intermediate",
        expected_minutes=expected_minutes,
        expected_followups=expected_followups,
        origin="generated",
    )


def v2_plan(
    config: PlanConfigurationSnapshot,
    questions: tuple[InterviewPlanQuestionV2, ...],
) -> InterviewPlanV2:
    return InterviewPlanV2(
        title="Configured interview",
        configuration_snapshot=config,
        questions=questions,
    )


@pytest.mark.parametrize(
    (
        "duration",
        "question_min",
        "question_max",
        "estimate_min",
        "estimate_max",
    ),
    (
        (15, 3, 4, 12, 20),
        (30, 5, 6, 24, 36),
        (45, 7, 8, 36, 54),
        (60, 9, 10, 48, 72),
    ),
)
def test_duration_profiles_match_the_frozen_plan(
    duration,
    question_min,
    question_max,
    estimate_min,
    estimate_max,
):
    profile = duration_budget_profile(duration)

    assert profile.recommended_main_questions_min == question_min
    assert profile.recommended_main_questions_max == question_max
    assert profile.acceptable_estimated_minutes_min == estimate_min
    assert profile.acceptable_estimated_minutes_max == estimate_max


def test_budget_policy_hash_freezes_profiles_formula_and_safe_range(monkeypatch):
    assert duration_budget_policy_sha256() == INTERVIEW_PLAN_BUDGET_CANONICAL_SHA256
    validate_duration_budget_policy()

    monkeypatch.setattr(budget_module, "FOLLOWUP_MINUTES", 3)
    with pytest.raises(ValueError, match="requires a new version"):
        validate_duration_budget_policy()


def test_estimate_formula_has_one_explicit_arithmetic_source():
    questions = (
        SimpleNamespace(expected_minutes=5, expected_followups=0),
        SimpleNamespace(expected_minutes=6, expected_followups=1),
        SimpleNamespace(expected_minutes=7, expected_followups=2),
    )

    estimate = estimate_plan_duration(questions)

    assert estimate.formula_version == INTERVIEW_PLAN_ESTIMATE_FORMULA_VERSION
    assert estimate.main_answer_minutes == 18
    assert estimate.expected_followup_count == 3
    assert estimate.expected_followup_minutes == 6
    assert estimate.transition_count == 2
    assert estimate.transition_minutes == 2
    assert estimate.estimated_minutes == 26


@pytest.mark.parametrize("invalid", (True, "5", -1, 61))
def test_estimator_rejects_coerced_or_out_of_range_main_answer_budget(invalid):
    with pytest.raises(ValueError):
        estimate_plan_duration(
            [SimpleNamespace(expected_minutes=invalid, expected_followups=0)]
        )


def test_default_legacy_conversion_closes_its_30_minute_estimate():
    legacy = InterviewPlan(
        title="Legacy",
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind=kind,
                prompt=f"Prompt {index}",
                focus=f"Focus {index}",
            )
            for index, kind in enumerate(
                ("project", "technical", "system-design"),
                start=1,
            )
        ],
    )

    converted = legacy_plan_to_v2(legacy)
    estimate = estimate_plan_duration(converted.questions)

    assert converted.configuration_snapshot.target_duration_minutes == 30
    assert sum(item.expected_followups for item in converted.questions) == 3
    assert estimate.estimated_minutes == 30


def test_followup_and_main_answer_allocations_are_bounded_and_close_target():
    followups = allocate_expected_followups(
        expected_followup_budget=7,
        question_count=5,
    )
    main_minutes = allocate_main_answer_minutes(
        target_duration_minutes=30,
        expected_followups=followups,
    )
    questions = tuple(
        SimpleNamespace(
            expected_minutes=minutes,
            expected_followups=followup_count,
        )
        for minutes, followup_count in zip(main_minutes, followups, strict=True)
    )

    assert sum(followups) == 7
    assert all(0 <= value <= 2 for value in followups)
    assert all(value >= 1 for value in main_minutes)
    assert estimate_plan_duration(questions).estimated_minutes == 30

    with pytest.raises(ValueError, match="hard limit"):
        allocate_expected_followups(
            expected_followup_budget=11,
            question_count=5,
        )


@pytest.mark.parametrize("question_count", (1, 10))
def test_schema_and_60_minute_configuration_allow_safe_question_range(
    question_count,
):
    type_budget = {"technical": question_count}
    config = configuration(
        duration=60,
        question_type_budget=type_budget,
        expected_followup_budget=question_count,
    )
    questions = tuple(
        question(index, expected_minutes=5)
        for index in range(1, question_count + 1)
    )

    created = v2_plan(config, questions)

    assert len(created.questions) == question_count


@pytest.mark.parametrize("question_count", (0, 11))
def test_schema_rejects_counts_outside_the_safe_range(question_count):
    config = configuration(
        duration=60,
        question_type_budget={"technical": max(1, question_count)},
        expected_followup_budget=min(question_count, 10),
    )
    questions = tuple(
        question(index, expected_minutes=5)
        for index in range(1, question_count + 1)
    )

    with pytest.raises(ValidationError, match="requires 1 to 10 questions"):
        v2_plan(config, questions)


def test_manual_deletion_is_warning_only_and_assessment_recalculates():
    config = configuration()
    original = v2_plan(
        config,
        tuple(
            question(index, question_type=kind, expected_minutes=4)
            for index, kind in enumerate(
                ("project", "project", "technical", "technical", "system-design"),
                start=1,
            )
        ),
    )
    edited_questions = tuple(
        item.model_copy(update={"position": index})
        for index, item in enumerate(original.questions[:3], start=1)
    )
    edited = v2_plan(config, edited_questions)

    before = assess_interview_plan_budget(original)
    after = assess_interview_plan_budget(edited)

    assert before.question_count == 5
    assert after.question_count == 3
    assert after.launch_allowed is True
    assert after.status == "WARNING"
    assert "below_recommended_question_count" in after.warning_codes
    assert "estimated_duration_below_acceptable" in after.warning_codes
    assert "question_type_budget_drift" in after.warning_codes
    assert "expected_followup_budget_drift" in after.warning_codes
    assert after.estimate.estimated_minutes < before.estimate.estimated_minutes


def test_assessment_blocks_only_empty_or_above_safe_count():
    config = configuration()
    valid = v2_plan(config, (question(1),))
    empty = valid.model_copy(update={"questions": ()})
    eleven = valid.model_copy(
        update={
            "questions": tuple(question(index) for index in range(1, 12))
        }
    )

    empty_assessment = assess_interview_plan_budget(empty)
    above_assessment = assess_interview_plan_budget(eleven)

    assert empty_assessment.blocking_codes == ("no_valid_questions",)
    assert above_assessment.blocking_codes == ("above_safe_question_count",)
    assert empty_assessment.launch_allowed is False
    assert above_assessment.launch_allowed is False


def test_assessment_revalidates_model_copy_before_trusting_duration_fields():
    config = configuration(question_type_budget={"technical": 1})
    invalid_question = question(1).model_copy(update={"expected_minutes": "5"})
    bypassed = v2_plan(config, (question(1),)).model_copy(
        update={"questions": (invalid_question,)}
    )

    with pytest.raises(ValidationError, match="expected_minutes must be an integer"):
        assess_interview_plan_budget(bypassed)


def test_assessment_model_rejects_status_or_launch_inconsistency():
    assessment = assess_interview_plan_budget(
        v2_plan(
            configuration(question_type_budget={"technical": 1}),
            (question(1),),
        )
    )
    payload = assessment.model_dump(mode="json")
    payload["status"] = "PASS"
    with pytest.raises(ValidationError, match="status is inconsistent"):
        PlanBudgetAssessment.model_validate(payload)


def test_configured_fallback_respects_type_count_duration_difficulty_and_focus():
    config = configuration(
        duration=60,
        question_type_budget={
            "project": 2,
            "technical": 3,
            "system-design": 2,
            "behavioral": 2,
        },
        difficulty="advanced",
        focus="system_design",
    )
    generated = fallback_interview_plan(config)
    contrasting = fallback_interview_plan(
        configuration(
            duration=60,
            question_type_budget=config.question_type_budget,
            difficulty="foundation",
            focus="project_review",
        )
    )

    assert len(generated.questions) == 9
    assert [item.id for item in generated.questions] == [
        f"q{index}" for index in range(1, 10)
    ]
    assert Counter(item.kind for item in generated.questions) == Counter(
        {
            "project": 2,
            "technical": 3,
            "system-design": 2,
            "behavioral": 2,
        }
    )
    assert validate_launchable_interview_plan(generated, config) is generated
    assert generated.title != contrasting.title
    assert [item.prompt for item in generated.questions] != [
        item.prompt for item in contrasting.questions
    ]
    assert [item.focus for item in generated.questions] != [
        item.focus for item in contrasting.questions
    ]


@pytest.mark.parametrize("question_count", (1, 10))
def test_configured_launch_validation_accepts_safe_bounds(question_count):
    config = configuration(
        duration=60,
        question_type_budget={"technical": question_count},
    )
    legacy = InterviewPlan(
        title="Safe",
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind="technical",
                prompt=f"Prompt {index}",
                focus=f"Focus {index}",
            )
            for index in range(1, question_count + 1)
        ],
    )

    assert validate_launchable_interview_plan(legacy, config) is legacy


def test_launch_validation_and_fallback_revalidate_bypassed_models():
    config = configuration(question_type_budget={"technical": 3})
    plan = InterviewPlan(
        title="Safe",
        questions=[
            InterviewQuestion(
                id=f"q{index}",
                kind="technical",
                prompt=f"Prompt {index}",
                focus=f"Focus {index}",
            )
            for index in range(1, 4)
        ],
    )
    invalid_plan = plan.model_copy(
        update={
            "questions": (
                plan.questions[0].model_copy(update={"id": " "}),
                *plan.questions[1:],
            )
        }
    )
    invalid_config = config.model_copy(
        update={"target_duration_minutes": 31}
    )

    with pytest.raises(ValidationError, match="id must not be blank"):
        validate_launchable_interview_plan(invalid_plan, config)
    with pytest.raises(ValidationError):
        validate_launchable_interview_plan(plan, invalid_config)
    with pytest.raises(ValidationError):
        fallback_interview_plan(invalid_config)


def test_frontend_has_no_duplicate_duration_arithmetic():
    root = Path(__file__).resolve().parents[1]
    frontend_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "frontend" / "src").rglob("*.js*")
    )

    assert "FOLLOWUP_MINUTES" not in frontend_source
    assert "TRANSITION_MINUTES" not in frontend_source
    assert "expected_followups * 2" not in frontend_source
    assert INTERVIEW_PLAN_BUDGET_VERSION == "interview-plan-duration-budget-v1"
