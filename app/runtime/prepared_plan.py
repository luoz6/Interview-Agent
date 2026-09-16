"""Runtime binding of generated plans to immutable revision snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.interview.plan_budget import assess_interview_plan_budget
from app.domain.interview.plan_generation import validate_generation_configuration
from app.domain.interview.plan_revision import (
    legacy_plan_to_v2,
    native_intent_plan_to_v3,
)
from app.domain.interview.prep import (
    InterviewPlan,
    PlanGenerationValidationError,
    v2_plan_to_legacy,
    validate_bound_plan_revision,
)
from app.runtime.config.environment import environment_value

if TYPE_CHECKING:
    from app.domain.interview.plan_revision import PlanConfigurationSnapshot
    from app.domain.knowledge.source_scope import InterviewKnowledgeScopeSnapshot


def bind_prepared_plan_revision(
    plan: InterviewPlan,
    configuration: PlanConfigurationSnapshot | None = None,
    *,
    knowledge_scope: InterviewKnowledgeScopeSnapshot | None = None,
) -> InterviewPlan:
    revision_plan = legacy_plan_to_v2(
        plan,
        configuration_snapshot=configuration,
        knowledge_scope=knowledge_scope,
    )
    assessment = assess_interview_plan_budget(revision_plan)
    if not assessment.launch_allowed:
        raise PlanGenerationValidationError(
            "generated_plan_not_launchable",
            "generated plan violates the launch safety boundary",
        )
    bound_legacy = v2_plan_to_legacy(revision_plan)
    plan.questions = bound_legacy.questions
    plan.prep_context = bound_legacy.prep_context
    jit_enabled = (
        str(environment_value("INTERVIEW_JIT_MAIN_QUESTION_ENABLED", "false"))
        .strip()
        .lower()
        == "true"
    )
    if jit_enabled and plan._intent_draft_questions:
        plan._revision_plan = native_intent_plan_to_v3(
            revision_plan,
            tuple(plan._intent_draft_questions),
        )
    elif jit_enabled:
        raise PlanGenerationValidationError(
            "provider_intent_payload_missing",
            "JIT plan generation requires a native intent payload",
        )
    else:
        plan._revision_plan = revision_plan
    return plan


def prepared_plan_revision(
    plan: InterviewPlan,
    configuration: PlanConfigurationSnapshot | None = None,
    *,
    knowledge_scope: InterviewKnowledgeScopeSnapshot | None = None,
):
    revision_plan = plan._revision_plan
    if revision_plan is None:
        bind_prepared_plan_revision(
            plan,
            configuration,
            knowledge_scope=knowledge_scope,
        )
    validated = validate_bound_plan_revision(plan)
    if (
        configuration is not None
        and validated.configuration_snapshot
        != validate_generation_configuration(configuration)
    ):
        raise PlanGenerationValidationError(
            "prepared_configuration_mismatch",
            "prepared plan configuration does not match the requested snapshot",
        )
    if knowledge_scope is not None:
        from app.domain.knowledge.source_scope import (
            InterviewKnowledgeScopeSnapshot,
        )

        requested_scope = InterviewKnowledgeScopeSnapshot.model_validate(
            knowledge_scope.model_dump(mode="json")
        )
        if validated.knowledge_scope != requested_scope:
            raise PlanGenerationValidationError(
                "prepared_knowledge_scope_mismatch",
                "prepared plan knowledge scope does not match the resolved snapshot",
            )
    return validated
