from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.interview_plan_revision import (
    PlanConfigurationSnapshot,
    canonical_sha256,
    plan_configuration_sha256,
)


PLAN_GENERATION_POLICY_SCHEMA_VERSION = (
    "interview-plan-generation-policy-v1"
)
PLAN_GENERATION_POLICY_VERSION = "interview-plan-config-strategy-v1"
PLAN_SCHEMA_VERSION = "interview-plan-v2"
PLAN_GENERATION_POLICY_CANONICAL_SHA256 = (
    "d37b1172bba95aa1623882fa630e6817955570e708af6b830e0c028bdcc1cbb7"
)

ConfigurationField = Literal[
    "difficulty",
    "target_duration_minutes",
    "focus_preset",
    "question_type_budget",
    "expected_followup_budget",
    "max_followups_per_question",
    "generator_version",
    "followup_policy_version",
]
GenerationEffect = Literal[
    "question_complexity",
    "question_budget_profile",
    "question_type_allocation",
    "prompt_emphasis",
    "duration_estimate",
    "followup_estimate",
    "runtime_followup_hard_limit",
    "generator_prompt_and_enforcement",
    "runtime_followup_decision_policy",
    "user_configuration_summary",
    "user_duration_warning",
]

EXPECTED_CONFIGURATION_FIELDS = (
    "difficulty",
    "target_duration_minutes",
    "focus_preset",
    "question_type_budget",
    "expected_followup_budget",
    "max_followups_per_question",
    "generator_version",
    "followup_policy_version",
)
EXPECTED_DIFFICULTIES = ("foundation", "intermediate", "advanced")
EXPECTED_DURATIONS = (15, 30, 45, 60)
EXPECTED_FOCUS_PRESETS = (
    "technical_depth",
    "system_design",
    "project_review",
    "balanced",
)
EXPECTED_QUESTION_TYPES = (
    "project",
    "technical",
    "system-design",
    "behavioral",
)
EXPECTED_FOLLOWUP_POLICIES = ("fixed_v1", "adaptive_v1")


class ImmutablePolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfigurationEffectPolicy(ImmutablePolicyModel):
    field: ConfigurationField
    generation_effects: tuple[GenerationEffect, ...] = Field(min_length=1)
    user_visible_semantics: str = Field(min_length=1)
    scoring_rubric_effect: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_effects(self):
        if len(self.generation_effects) != len(set(self.generation_effects)):
            raise ValueError("configuration generation effects must be unique")
        return self


class QuestionTypeBudgetPolicy(ImmutablePolicyModel):
    mode: Literal["exact_generation_target_by_type"]
    sparse_missing_type_count: Literal[0]
    zero_count_allowed: Literal[True]
    zero_total_allowed: Literal[False]
    negative_count_allowed: Literal[False]
    boolean_count_allowed: Literal[False]
    manual_edit_may_diverge_from_generation_target: Literal[True]


class ExpectedFollowupBudgetPolicy(ImmutablePolicyModel):
    mode: Literal["aggregate_expected_count_for_estimation"]
    runtime_hard_cap: Literal[False]
    adaptive_runtime_may_use_fewer: Literal[True]
    per_question_hard_cap_field: Literal["max_followups_per_question"]


class DurationPolicy(ImmutablePolicyModel):
    meaning: Literal["target_estimate_not_sla"]
    question_budget_model_owner: Literal["T51"]
    launch_validation_owner: Literal["T51_T52"]
    provider_budget_enforcement_owner: Literal["T52"]
    user_warning_owner: Literal["T54"]
    configuration_values_frozen_in_t50: Literal[True]
    question_ranges_frozen_in_t50: Literal[False]


class ScoringSeparationPolicy(ImmutablePolicyModel):
    configuration_may_change_rubric: Literal[False]
    configuration_may_change_passing_threshold: Literal[False]
    rubric_source: Literal["report_rule_score"]
    evaluation_uses_answer_evidence_not_plan_preset: Literal[True]


class LegacyCompatibilityPolicy(ImmutablePolicyModel):
    v1_parser_retained: Literal[True]
    v1_default_difficulty: Literal["intermediate"]
    v1_default_target_duration_minutes: Literal[30]
    v1_default_focus_preset: Literal["balanced"]
    v1_default_followup_policy_version: Literal["fixed_v1"]
    legacy_parse_does_not_call_provider: Literal[True]


class InterviewPlanGenerationPolicy(ImmutablePolicyModel):
    schema_version: Literal["interview-plan-generation-policy-v1"]
    policy_version: Literal["interview-plan-config-strategy-v1"]
    plan_schema_version: Literal["interview-plan-v2"]
    allowed_difficulties: tuple[str, ...]
    allowed_target_duration_minutes: tuple[int, ...]
    allowed_focus_presets: tuple[str, ...]
    allowed_question_types: tuple[str, ...]
    allowed_followup_policy_versions: tuple[str, ...]
    max_followups_per_question: Literal[2]
    snapshot_hash_fields: tuple[ConfigurationField, ...]
    configuration_effects: tuple[ConfigurationEffectPolicy, ...]
    question_type_budget: QuestionTypeBudgetPolicy
    expected_followup_budget: ExpectedFollowupBudgetPolicy
    duration: DurationPolicy
    scoring_separation: ScoringSeparationPolicy
    legacy_compatibility: LegacyCompatibilityPolicy

    @model_validator(mode="after")
    def validate_frozen_contract(self):
        expected_values = (
            (self.allowed_difficulties, EXPECTED_DIFFICULTIES, "difficulties"),
            (
                self.allowed_target_duration_minutes,
                EXPECTED_DURATIONS,
                "durations",
            ),
            (
                self.allowed_focus_presets,
                EXPECTED_FOCUS_PRESETS,
                "focus presets",
            ),
            (
                self.allowed_question_types,
                EXPECTED_QUESTION_TYPES,
                "question types",
            ),
            (
                self.allowed_followup_policy_versions,
                EXPECTED_FOLLOWUP_POLICIES,
                "followup policies",
            ),
            (
                self.snapshot_hash_fields,
                EXPECTED_CONFIGURATION_FIELDS,
                "snapshot hash fields",
            ),
        )
        for actual, expected, label in expected_values:
            if tuple(actual) != tuple(expected):
                raise ValueError(f"frozen plan {label} changed")
        effect_fields = tuple(item.field for item in self.configuration_effects)
        if len(effect_fields) != len(set(effect_fields)):
            raise ValueError("duplicate configuration effect field")
        if set(effect_fields) != set(EXPECTED_CONFIGURATION_FIELDS):
            raise ValueError("configuration effect fields are incomplete")
        return self


class PlanConfigurationPolicyResult(ImmutablePolicyModel):
    status: Literal["PASS"]
    policy_version: str
    plan_schema_version: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    main_question_generation_target: int = Field(ge=1)
    expected_followup_budget: int = Field(ge=0)
    max_followups_per_question: Literal[2]
    scoring_rubric_changed: Literal[False]


def load_interview_plan_generation_policy(
    path: Path | str,
) -> InterviewPlanGenerationPolicy:
    policy = InterviewPlanGenerationPolicy.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    return _validated_policy(policy)


def evaluate_plan_configuration_policy(
    configuration: PlanConfigurationSnapshot,
    policy: InterviewPlanGenerationPolicy,
) -> PlanConfigurationPolicyResult:
    policy = _validated_policy(policy)
    configuration = PlanConfigurationSnapshot.model_validate(
        configuration.model_dump(mode="json")
    )
    if configuration.difficulty not in policy.allowed_difficulties:
        raise ValueError("difficulty is outside the frozen generation policy")
    if (
        configuration.target_duration_minutes
        not in policy.allowed_target_duration_minutes
    ):
        raise ValueError("target duration is outside the frozen generation policy")
    if configuration.focus_preset not in policy.allowed_focus_presets:
        raise ValueError("focus preset is outside the frozen generation policy")
    if any(
        question_type not in policy.allowed_question_types
        for question_type in configuration.question_type_budget
    ):
        raise ValueError("question type is outside the frozen generation policy")
    if (
        configuration.followup_policy_version
        not in policy.allowed_followup_policy_versions
    ):
        raise ValueError("followup policy is outside the frozen generation policy")
    if (
        configuration.max_followups_per_question
        != policy.max_followups_per_question
    ):
        raise ValueError("per-question followup hard limit changed")
    return PlanConfigurationPolicyResult(
        status="PASS",
        policy_version=policy.policy_version,
        plan_schema_version=policy.plan_schema_version,
        configuration_sha256=plan_configuration_sha256(configuration),
        main_question_generation_target=sum(
            configuration.question_type_budget.values()
        ),
        expected_followup_budget=configuration.expected_followup_budget,
        max_followups_per_question=configuration.max_followups_per_question,
        scoring_rubric_changed=False,
    )


def _validated_policy(
    policy: InterviewPlanGenerationPolicy,
) -> InterviewPlanGenerationPolicy:
    validated = InterviewPlanGenerationPolicy.model_validate(
        policy.model_dump(mode="json")
    )
    if canonical_sha256(validated) != PLAN_GENERATION_POLICY_CANONICAL_SHA256:
        raise ValueError(
            "interview plan generation policy hash drift requires a new version"
        )
    return validated
