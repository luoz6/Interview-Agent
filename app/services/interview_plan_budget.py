from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


INTERVIEW_PLAN_BUDGET_VERSION = "interview-plan-duration-budget-v1"
INTERVIEW_PLAN_ESTIMATE_FORMULA_VERSION = (
    "main-answer-plus-followups-plus-transitions-v1"
)
INTERVIEW_PLAN_BUDGET_CANONICAL_SHA256 = (
    "4f7213f4dd010032c75c61fa6ee7adf9941868912dbbf35f0deb75e4b3ca3b8b"
)
MIN_SAFE_MAIN_QUESTION_COUNT = 1
MAX_SAFE_MAIN_QUESTION_COUNT = 10
FOLLOWUP_MINUTES = 2
TRANSITION_MINUTES = 1
QUESTION_TYPE_ORDER = (
    "project",
    "technical",
    "system-design",
    "behavioral",
)

BudgetWarningCode = Literal[
    "below_recommended_question_count",
    "above_recommended_question_count",
    "estimated_duration_below_acceptable",
    "estimated_duration_above_acceptable",
    "question_type_budget_drift",
    "expected_followup_budget_drift",
]
BudgetBlockingCode = Literal[
    "no_valid_questions",
    "above_safe_question_count",
]


class ImmutableBudgetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DurationBudgetProfile(ImmutableBudgetModel):
    target_duration_minutes: Literal[15, 30, 45, 60]
    recommended_main_questions_min: int = Field(ge=1)
    recommended_main_questions_max: int = Field(ge=1)
    acceptable_estimated_minutes_min: int = Field(ge=1)
    acceptable_estimated_minutes_max: int = Field(ge=1)

    @field_validator(
        "target_duration_minutes",
        "recommended_main_questions_min",
        "recommended_main_questions_max",
        "acceptable_estimated_minutes_min",
        "acceptable_estimated_minutes_max",
        mode="before",
    )
    @classmethod
    def require_strict_integers(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("duration budget values must be integers")
        return value

    @model_validator(mode="after")
    def validate_ranges(self):
        if (
            self.recommended_main_questions_min
            > self.recommended_main_questions_max
        ):
            raise ValueError("recommended question range is inverted")
        if (
            self.acceptable_estimated_minutes_min
            > self.acceptable_estimated_minutes_max
        ):
            raise ValueError("acceptable duration range is inverted")
        return self


_DURATION_BUDGET_PROFILE_ITEMS = (
    DurationBudgetProfile(
        target_duration_minutes=15,
        recommended_main_questions_min=3,
        recommended_main_questions_max=4,
        acceptable_estimated_minutes_min=12,
        acceptable_estimated_minutes_max=20,
    ),
    DurationBudgetProfile(
        target_duration_minutes=30,
        recommended_main_questions_min=5,
        recommended_main_questions_max=6,
        acceptable_estimated_minutes_min=24,
        acceptable_estimated_minutes_max=36,
    ),
    DurationBudgetProfile(
        target_duration_minutes=45,
        recommended_main_questions_min=7,
        recommended_main_questions_max=8,
        acceptable_estimated_minutes_min=36,
        acceptable_estimated_minutes_max=54,
    ),
    DurationBudgetProfile(
        target_duration_minutes=60,
        recommended_main_questions_min=9,
        recommended_main_questions_max=10,
        acceptable_estimated_minutes_min=48,
        acceptable_estimated_minutes_max=72,
    ),
)
DURATION_BUDGET_PROFILES = MappingProxyType(
    {
        profile.target_duration_minutes: profile
        for profile in _DURATION_BUDGET_PROFILE_ITEMS
    }
)


class PlanDurationEstimate(ImmutableBudgetModel):
    formula_version: Literal[
        "main-answer-plus-followups-plus-transitions-v1"
    ]
    main_answer_minutes: int = Field(ge=0)
    expected_followup_count: int = Field(ge=0)
    followup_minutes_each: Literal[2]
    expected_followup_minutes: int = Field(ge=0)
    transition_count: int = Field(ge=0)
    transition_minutes_each: Literal[1]
    transition_minutes: int = Field(ge=0)
    estimated_minutes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_arithmetic(self):
        if (
            self.expected_followup_minutes
            != self.expected_followup_count * self.followup_minutes_each
        ):
            raise ValueError("expected follow-up duration arithmetic drifted")
        if (
            self.transition_minutes
            != self.transition_count * self.transition_minutes_each
        ):
            raise ValueError("transition duration arithmetic drifted")
        if self.estimated_minutes != (
            self.main_answer_minutes
            + self.expected_followup_minutes
            + self.transition_minutes
        ):
            raise ValueError("estimated duration arithmetic drifted")
        return self


class PlanBudgetAssessment(ImmutableBudgetModel):
    budget_version: Literal["interview-plan-duration-budget-v1"]
    status: Literal["PASS", "WARNING", "BLOCKED"]
    launch_allowed: bool
    target_duration_minutes: Literal[15, 30, 45, 60]
    question_count: int = Field(ge=0)
    safe_question_count_min: Literal[1]
    safe_question_count_max: Literal[10]
    recommended_question_count_min: int = Field(ge=1)
    recommended_question_count_max: int = Field(ge=1)
    acceptable_estimated_minutes_min: int = Field(ge=1)
    acceptable_estimated_minutes_max: int = Field(ge=1)
    expected_question_type_budget: dict[str, int]
    actual_question_type_count: dict[str, int]
    configured_expected_followup_budget: int = Field(ge=0)
    estimate: PlanDurationEstimate
    warning_codes: tuple[BudgetWarningCode, ...]
    blocking_codes: tuple[BudgetBlockingCode, ...]

    @model_validator(mode="after")
    def validate_status(self):
        expected_status = (
            "BLOCKED"
            if self.blocking_codes
            else ("WARNING" if self.warning_codes else "PASS")
        )
        if self.status != expected_status:
            raise ValueError("budget assessment status is inconsistent")
        if self.launch_allowed != (not self.blocking_codes):
            raise ValueError("launch_allowed is inconsistent with blocking codes")
        if len(self.warning_codes) != len(set(self.warning_codes)):
            raise ValueError("budget warning codes must be unique")
        if len(self.blocking_codes) != len(set(self.blocking_codes)):
            raise ValueError("budget blocking codes must be unique")
        return self


def duration_budget_policy_payload() -> dict[str, Any]:
    return {
        "budget_version": INTERVIEW_PLAN_BUDGET_VERSION,
        "formula_version": INTERVIEW_PLAN_ESTIMATE_FORMULA_VERSION,
        "safe_main_question_count": {
            "minimum": MIN_SAFE_MAIN_QUESTION_COUNT,
            "maximum": MAX_SAFE_MAIN_QUESTION_COUNT,
        },
        "formula": {
            "followup_minutes_each": FOLLOWUP_MINUTES,
            "transition_minutes_each": TRANSITION_MINUTES,
        },
        "profiles": [
            profile.model_dump(mode="json")
            for profile in _DURATION_BUDGET_PROFILE_ITEMS
        ],
    }


def duration_budget_policy_sha256() -> str:
    encoded = json.dumps(
        duration_budget_policy_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def validate_duration_budget_policy() -> None:
    if len(DURATION_BUDGET_PROFILES) != 4:
        raise ValueError("duration budget profiles are incomplete")
    actual_hash = duration_budget_policy_sha256()
    if actual_hash != INTERVIEW_PLAN_BUDGET_CANONICAL_SHA256:
        raise ValueError(
            "interview plan duration budget drift requires a new version"
        )


def duration_budget_profile(
    target_duration_minutes: int,
) -> DurationBudgetProfile:
    if (
        isinstance(target_duration_minutes, bool)
        or not isinstance(target_duration_minutes, int)
    ):
        raise ValueError("target_duration_minutes must be an integer")
    validate_duration_budget_policy()
    try:
        return DURATION_BUDGET_PROFILES[target_duration_minutes]
    except KeyError as exc:
        raise ValueError(
            "target_duration_minutes must be one of 15, 30, 45, or 60"
        ) from exc


def estimate_plan_duration(
    questions: list[Any] | tuple[Any, ...],
) -> PlanDurationEstimate:
    if not isinstance(questions, (list, tuple)):
        raise ValueError("questions must be a list or tuple")
    main_answer_minutes = 0
    expected_followup_count = 0
    for question in questions:
        expected_minutes = _strict_integer_attribute(
            question,
            "expected_minutes",
            minimum=1,
            maximum=60,
        )
        expected_followups = _strict_integer_attribute(
            question,
            "expected_followups",
            minimum=0,
            maximum=2,
        )
        main_answer_minutes += expected_minutes
        expected_followup_count += expected_followups
    expected_followup_minutes = expected_followup_count * FOLLOWUP_MINUTES
    transition_count = max(0, len(questions) - 1)
    transition_minutes = transition_count * TRANSITION_MINUTES
    return PlanDurationEstimate(
        formula_version=INTERVIEW_PLAN_ESTIMATE_FORMULA_VERSION,
        main_answer_minutes=main_answer_minutes,
        expected_followup_count=expected_followup_count,
        followup_minutes_each=FOLLOWUP_MINUTES,
        expected_followup_minutes=expected_followup_minutes,
        transition_count=transition_count,
        transition_minutes_each=TRANSITION_MINUTES,
        transition_minutes=transition_minutes,
        estimated_minutes=(
            main_answer_minutes
            + expected_followup_minutes
            + transition_minutes
        ),
    )


def assess_interview_plan_budget(plan: Any) -> PlanBudgetAssessment:
    from app.services.interview_plan_revision import (
        InterviewPlanQuestionV2,
        PlanConfigurationSnapshot,
    )

    configuration_value = getattr(plan, "configuration_snapshot", None)
    configuration = PlanConfigurationSnapshot.model_validate(
        _model_payload(configuration_value)
    )
    raw_questions = getattr(plan, "questions", None)
    if not isinstance(raw_questions, (list, tuple)):
        raise ValueError("plan questions must be a list or tuple")
    questions = tuple(
        InterviewPlanQuestionV2.model_validate(_model_payload(question))
        for question in raw_questions
    )
    profile = duration_budget_profile(configuration.target_duration_minutes)
    estimate = estimate_plan_duration(questions)
    question_count = len(questions)
    warnings: list[BudgetWarningCode] = []
    blocking: list[BudgetBlockingCode] = []

    if question_count < MIN_SAFE_MAIN_QUESTION_COUNT:
        blocking.append("no_valid_questions")
    elif question_count > MAX_SAFE_MAIN_QUESTION_COUNT:
        blocking.append("above_safe_question_count")
    if question_count < profile.recommended_main_questions_min:
        warnings.append("below_recommended_question_count")
    elif question_count > profile.recommended_main_questions_max:
        warnings.append("above_recommended_question_count")
    if (
        estimate.estimated_minutes
        < profile.acceptable_estimated_minutes_min
    ):
        warnings.append("estimated_duration_below_acceptable")
    elif (
        estimate.estimated_minutes
        > profile.acceptable_estimated_minutes_max
    ):
        warnings.append("estimated_duration_above_acceptable")

    expected_types = _normalized_question_type_counts(
        configuration.question_type_budget
    )
    actual_types = _normalized_question_type_counts(
        Counter(question.question_type for question in questions)
    )
    if actual_types != expected_types:
        warnings.append("question_type_budget_drift")
    if (
        estimate.expected_followup_count
        != configuration.expected_followup_budget
    ):
        warnings.append("expected_followup_budget_drift")

    status = "BLOCKED" if blocking else ("WARNING" if warnings else "PASS")
    return PlanBudgetAssessment(
        budget_version=INTERVIEW_PLAN_BUDGET_VERSION,
        status=status,
        launch_allowed=not blocking,
        target_duration_minutes=configuration.target_duration_minutes,
        question_count=question_count,
        safe_question_count_min=MIN_SAFE_MAIN_QUESTION_COUNT,
        safe_question_count_max=MAX_SAFE_MAIN_QUESTION_COUNT,
        recommended_question_count_min=(
            profile.recommended_main_questions_min
        ),
        recommended_question_count_max=(
            profile.recommended_main_questions_max
        ),
        acceptable_estimated_minutes_min=(
            profile.acceptable_estimated_minutes_min
        ),
        acceptable_estimated_minutes_max=(
            profile.acceptable_estimated_minutes_max
        ),
        expected_question_type_budget=expected_types,
        actual_question_type_count=actual_types,
        configured_expected_followup_budget=(
            configuration.expected_followup_budget
        ),
        estimate=estimate,
        warning_codes=tuple(warnings),
        blocking_codes=tuple(blocking),
    )


def allocate_expected_followups(
    *,
    expected_followup_budget: int,
    question_count: int,
    max_followups_per_question: int = 2,
) -> tuple[int, ...]:
    _require_strict_integer(
        expected_followup_budget,
        "expected followup budget",
        minimum=0,
    )
    _require_strict_integer(
        question_count,
        "question count",
        minimum=0,
    )
    _require_strict_integer(
        max_followups_per_question,
        "max followups per question",
        minimum=0,
    )
    if question_count < MIN_SAFE_MAIN_QUESTION_COUNT:
        raise ValueError("at least one question is required")
    if question_count > MAX_SAFE_MAIN_QUESTION_COUNT:
        raise ValueError("question count exceeds the safe maximum")
    if expected_followup_budget < 0:
        raise ValueError("expected followup budget must be non-negative")
    if expected_followup_budget > question_count * max_followups_per_question:
        raise ValueError("expected followup budget exceeds the hard limit")
    allocation = [0] * question_count
    for index in range(expected_followup_budget):
        allocation[index % question_count] += 1
    return tuple(allocation)


def allocate_main_answer_minutes(
    *,
    target_duration_minutes: int,
    expected_followups: tuple[int, ...],
) -> tuple[int, ...]:
    duration_budget_profile(target_duration_minutes)
    if not isinstance(expected_followups, tuple):
        raise ValueError("expected_followups must be a tuple")
    for value in expected_followups:
        _require_strict_integer(
            value,
            "expected followup allocation",
            minimum=0,
            maximum=2,
        )
    question_count = len(expected_followups)
    if question_count < MIN_SAFE_MAIN_QUESTION_COUNT:
        raise ValueError("at least one question is required")
    transition_minutes = (question_count - 1) * TRANSITION_MINUTES
    followup_minutes = sum(expected_followups) * FOLLOWUP_MINUTES
    available = target_duration_minutes - transition_minutes - followup_minutes
    available = max(question_count, available)
    quotient, remainder = divmod(available, question_count)
    return tuple(
        quotient + (1 if index < remainder else 0)
        for index in range(question_count)
    )


def _normalized_question_type_counts(values: Any) -> dict[str, int]:
    return {
        question_type: int(values.get(question_type, 0))
        for question_type in QUESTION_TYPE_ORDER
    }


def _model_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", warnings=False)
    return value


def _strict_integer_attribute(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        candidate = getattr(value, name)
    except AttributeError as exc:
        raise ValueError(f"question is missing {name}") from exc
    return _require_strict_integer(
        candidate,
        name,
        minimum=minimum,
        maximum=maximum,
    )


def _require_strict_integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return value
