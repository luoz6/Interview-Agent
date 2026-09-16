"""Deterministic validation for provider-generated interview plans."""

from __future__ import annotations

from collections import Counter

from app.domain.interview.plan_budget import (
    MAX_SAFE_MAIN_QUESTION_COUNT,
    MIN_SAFE_MAIN_QUESTION_COUNT,
    QUESTION_TYPE_ORDER,
)
from app.domain.interview.plan_revision import (
    DEFAULT_PLAN_GENERATOR_VERSION,
    PlanConfigurationSnapshot,
)
from app.domain.interview.prep import (
    InterviewIntentDraftPlan,
    InterviewPlan,
    InterviewQuestion,
    PlanGenerationValidationError,
)
from app.domain.interview.question_quality import (
    hard_interview_question_quality_findings,
)


def interview_plan_from_intent_draft(
    draft: InterviewIntentDraftPlan,
) -> InterviewPlan:
    plan = InterviewPlan(
        title=draft.title,
        questions=[
            InterviewQuestion(
                id=item.id,
                kind=item.kind,
                focus=item.focus,
                # Transient compatibility projection, never final wording.
                prompt=item.focus,
            )
            for item in draft.questions
        ],
    )
    plan._intent_draft_questions = tuple(draft.questions)
    return plan


def enforce_generated_intent_plan(
    plan: InterviewPlan,
    configuration: PlanConfigurationSnapshot | None = None,
) -> InterviewPlan:
    intents = tuple(plan._intent_draft_questions)
    if not intents or len(intents) != len(plan.questions):
        raise PlanGenerationValidationError(
            "provider_intent_payload_missing",
            "Provider intent plan is missing its typed intent payload",
        )
    expected_ids = [f"q{index}" for index in range(1, len(intents) + 1)]
    if [item.id for item in intents] != expected_ids:
        raise PlanGenerationValidationError(
            "provider_question_sequence_invalid",
            "Provider intent IDs must be unique and consecutive q1..qN",
        )
    normalized_focuses = [" ".join(item.focus.casefold().split()) for item in intents]
    if len(normalized_focuses) != len(set(normalized_focuses)):
        raise PlanGenerationValidationError(
            "provider_duplicate_intent_focus",
            "Provider returned duplicate intent focus values",
        )
    if configuration is None:
        if not 3 <= len(intents) <= 5:
            raise PlanGenerationValidationError(
                "provider_question_count_invalid",
                "Provider must return 3 to 5 intents",
            )
    else:
        configuration = validate_generation_configuration(configuration)
        expected_count = sum(configuration.question_type_budget.values())
        if len(intents) != expected_count:
            raise PlanGenerationValidationError(
                "provider_question_count_mismatch",
                "Provider intent count does not match the configured target",
            )
        actual_budget = Counter(item.kind for item in intents)
        expected_budget = {
            kind: count
            for kind, count in configuration.question_type_budget.items()
            if count
        }
        if dict(actual_budget) != expected_budget:
            raise PlanGenerationValidationError(
                "provider_question_type_budget_mismatch",
                "Provider intent types do not match the configured budget",
            )
    plan._generation_enforcement = {
        "action": "accepted",
        "provider_question_count": len(intents),
        "retained_question_count": len(intents),
        "intent_only": True,
    }
    return plan


def enforce_generated_interview_question_quality(
    plan: InterviewPlan,
) -> InterviewPlan:
    """Reject only deterministic Hard findings at a generation boundary."""

    hard_findings = hard_interview_question_quality_findings(tuple(plan.questions))
    if hard_findings:
        violation = hard_findings[0]
        raise PlanGenerationValidationError(
            violation.code,
            violation.evidence_summary,
        )
    return plan


def validate_generation_configuration(
    configuration: PlanConfigurationSnapshot,
) -> PlanConfigurationSnapshot:
    validated = PlanConfigurationSnapshot.model_validate(
        configuration.model_dump(mode="json", warnings=False)
    )
    if validated.generator_version != DEFAULT_PLAN_GENERATOR_VERSION:
        raise PlanGenerationValidationError(
            "unsupported_plan_generator_version",
            "requested plan generator version is not deployed",
        )
    question_count = sum(validated.question_type_budget.values())
    if not (
        MIN_SAFE_MAIN_QUESTION_COUNT
        <= question_count
        <= MAX_SAFE_MAIN_QUESTION_COUNT
    ):
        raise PlanGenerationValidationError(
            "configured_question_count_out_of_range",
            "configured generation question count must be 1 to 10",
        )
    if (
        validated.expected_followup_budget
        > question_count * validated.max_followups_per_question
    ):
        raise PlanGenerationValidationError(
            "configured_followup_budget_exceeds_hard_limit",
            "configured expected follow-up budget exceeds the per-question hard limit",
        )
    return validated


def enforce_generated_interview_plan(
    plan: InterviewPlan,
    configuration: PlanConfigurationSnapshot,
) -> InterviewPlan:
    """Validate and deterministically enforce one configured Provider result."""

    prior_enforcement = dict(plan._generation_enforcement)
    configuration = validate_generation_configuration(configuration)
    validated = InterviewPlan.model_validate(
        plan.model_dump(mode="json", warnings=False)
    )
    provider_count = len(validated.questions)
    target_count = sum(configuration.question_type_budget.values())
    expected_ids = [f"q{index}" for index in range(1, provider_count + 1)]
    actual_ids = [question.id for question in validated.questions]
    if actual_ids != expected_ids:
        raise PlanGenerationValidationError(
            "provider_question_sequence_invalid",
            "Provider question IDs must be unique and consecutive q1..qN",
        )
    if provider_count < target_count:
        raise PlanGenerationValidationError(
            "provider_question_count_under_budget",
            "Provider returned fewer questions than the configured target",
        )
    if provider_count > MAX_SAFE_MAIN_QUESTION_COUNT:
        raise PlanGenerationValidationError(
            "provider_question_count_above_safe_maximum",
            "Provider returned more than the safe maximum of 10 questions",
        )

    retained = list(validated.questions[:target_count])
    normalized_prompts = [
        " ".join(question.prompt.casefold().split()) for question in retained
    ]
    if len(normalized_prompts) != len(set(normalized_prompts)):
        raise PlanGenerationValidationError(
            "provider_duplicate_question",
            "Provider returned duplicate question text",
        )
    expected_types = {
        question_type: configuration.question_type_budget.get(question_type, 0)
        for question_type in QUESTION_TYPE_ORDER
    }
    actual_counter = Counter(question.kind for question in retained)
    actual_types = {
        question_type: actual_counter.get(question_type, 0)
        for question_type in QUESTION_TYPE_ORDER
    }
    if actual_types != expected_types:
        raise PlanGenerationValidationError(
            "provider_question_type_budget_mismatch",
            "Provider question types do not match the configured exact budget",
        )

    enforced = validated.model_copy(update={"questions": retained})
    enforced._generation_enforcement = prior_enforcement or {
        "action": "trimmed" if provider_count > target_count else "accepted",
        "provider_question_count": provider_count,
        "retained_question_count": target_count,
    }
    launchable = validate_launchable_interview_plan(enforced, configuration)
    return enforce_generated_interview_question_quality(launchable)


def validate_launchable_interview_plan(
    plan: InterviewPlan,
    configuration: PlanConfigurationSnapshot | None = None,
) -> InterviewPlan:
    validated_plan = InterviewPlan.model_validate(
        plan.model_dump(mode="json", warnings=False)
    )
    question_ids = [question.id for question in validated_plan.questions]
    if configuration is None:
        minimum, maximum = 3, 5
    else:
        PlanConfigurationSnapshot.model_validate(
            configuration.model_dump(mode="json", warnings=False)
        )
        minimum = MIN_SAFE_MAIN_QUESTION_COUNT
        maximum = MAX_SAFE_MAIN_QUESTION_COUNT
    if not minimum <= len(question_ids) <= maximum:
        raise ValueError(
            f"launchable interview plans require {minimum} to {maximum} questions"
        )
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("launchable interview question ids must be unique")
    expected_ids = [f"q{index}" for index in range(1, len(question_ids) + 1)]
    if question_ids != expected_ids:
        raise ValueError("launchable interview question ids must be consecutive q1..qN")
    return plan
