from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.interview_plan_audit import (
    PlanAuditFieldDiff,
    PlanAuditOperation,
    PlanRevisionAudit,
)

from app.services.interview_plan_generation_policy import (
    EXPECTED_CONFIGURATION_FIELDS,
    EXPECTED_DIFFICULTIES,
    EXPECTED_DURATIONS,
    EXPECTED_FOCUS_PRESETS,
    EXPECTED_FOLLOWUP_POLICIES,
    EXPECTED_QUESTION_TYPES,
    PLAN_GENERATION_POLICY_CANONICAL_SHA256,
    evaluate_plan_configuration_policy,
    load_interview_plan_generation_policy,
)
from app.services.interview_plan_revision import (
    InterviewPlanRevision,
    PlanConfigurationSnapshot,
    canonical_sha256,
    legacy_plan_to_v2,
    plan_configuration_sha256,
    plan_payload_sha256,
    v2_plan_to_legacy,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report_rule_score import REPORT_SCORING_RUBRIC_VERSION
from tests.test_interview_plan_revision import configuration, plan, source


POLICY_PATH = Path("config/interview_plan_generation_policy_v1.json")


def _policy():
    return load_interview_plan_generation_policy(POLICY_PATH)


def _configuration(**updates):
    payload = configuration().model_dump(mode="json")
    payload.update(updates)
    return PlanConfigurationSnapshot.model_validate(payload)


def _legacy_plan():
    return InterviewPlan(
        title="Legacy interview plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe a relevant project.",
                focus="project ownership",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain a cache consistency trade-off.",
                focus="technical depth",
            ),
            InterviewQuestion(
                id="q3",
                kind="system-design",
                prompt="Design a resilient API.",
                focus="system design",
            ),
        ],
    )


def test_t50_policy_freezes_the_existing_schema_v2_configuration_surface():
    policy = _policy()

    assert policy.plan_schema_version == "interview-plan-v2"
    assert policy.policy_version == "interview-plan-config-strategy-v1"
    assert policy.allowed_difficulties == EXPECTED_DIFFICULTIES
    assert policy.allowed_target_duration_minutes == EXPECTED_DURATIONS
    assert policy.allowed_focus_presets == EXPECTED_FOCUS_PRESETS
    assert policy.allowed_question_types == EXPECTED_QUESTION_TYPES
    assert policy.allowed_followup_policy_versions == EXPECTED_FOLLOWUP_POLICIES
    assert policy.snapshot_hash_fields == EXPECTED_CONFIGURATION_FIELDS
    assert policy.max_followups_per_question == 2
    assert canonical_sha256(policy) == (
        PLAN_GENERATION_POLICY_CANONICAL_SHA256
    )
    assert set(PlanConfigurationSnapshot.model_fields) == set(
        EXPECTED_CONFIGURATION_FIELDS
    )


def test_all_t50_duration_difficulty_focus_combinations_are_valid():
    policy = _policy()
    results = []
    for duration in EXPECTED_DURATIONS:
        for difficulty in EXPECTED_DIFFICULTIES:
            for focus in EXPECTED_FOCUS_PRESETS:
                config = _configuration(
                    target_duration_minutes=duration,
                    difficulty=difficulty,
                    focus_preset=focus,
                )
                results.append(
                    evaluate_plan_configuration_policy(config, policy)
                )

    assert len(results) == 48
    assert all(result.status == "PASS" for result in results)
    assert all(result.max_followups_per_question == 2 for result in results)
    assert all(result.scoring_rubric_changed is False for result in results)


def test_configuration_and_every_generation_strategy_field_enter_the_hash():
    base = configuration()
    base_plan = plan()
    base_configuration_hash = plan_configuration_sha256(base)
    base_plan_hash = plan_payload_sha256(base_plan)
    variations = [
        _configuration(difficulty="advanced"),
        _configuration(target_duration_minutes=45),
        _configuration(focus_preset="technical_depth"),
        _configuration(
            question_type_budget={"project": 1, "technical": 2}
        ),
        _configuration(expected_followup_budget=2),
        _configuration(generator_version="plan-generator-v2-next"),
        _configuration(followup_policy_version="adaptive_v1"),
    ]

    for changed in variations:
        assert plan_configuration_sha256(changed) != base_configuration_hash
        changed_plan = base_plan.model_copy(
            update={"configuration_snapshot": changed}
        )
        assert plan_payload_sha256(changed_plan) != base_plan_hash

    reordered = _configuration(
        question_type_budget={
            "system-design": 1,
            "project": 1,
            "technical": 1,
        }
    )
    assert plan_configuration_sha256(reordered) == base_configuration_hash


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("question_type_budget", {}, "at least one question"),
        (
            "question_type_budget",
            {"technical": True},
            "non-negative integers",
        ),
        (
            "question_type_budget",
            {"technical": "1"},
            "non-negative integers",
        ),
        (
            "expected_followup_budget",
            True,
            "must be an integer",
        ),
        (
            "expected_followup_budget",
            "3",
            "must be an integer",
        ),
        (
            "followup_policy_version",
            "unfrozen_policy",
            "Input should be",
        ),
        (
            "generator_version",
            "Plan Generator V2",
            "String should match pattern",
        ),
    ],
)
def test_configuration_rejects_ambiguous_or_unversioned_values(
    field,
    value,
    message,
):
    payload = configuration().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        PlanConfigurationSnapshot.model_validate(payload)


def test_revision_generator_version_must_match_the_hashed_snapshot():
    current_plan = plan()
    payload = {
        "plan_revision_id": "11111111-1111-4111-8111-111111111111",
        "plan_family_id": "22222222-2222-4222-8222-222222222222",
        "revision": 1,
        "parent_revision_id": None,
        "source_kind": "generated",
        "source_id": "33333333-3333-4333-8333-333333333333",
        "source_sha256": "a" * 64,
        "configuration_snapshot": current_plan.configuration_snapshot,
        "plan": current_plan,
        "plan_sha256": plan_payload_sha256(current_plan),
        "generator_version": "different-generator-version",
        "created_at": "2026-08-06T00:00:00Z",
        "created_reason": "initial_generation",
        "audit": PlanRevisionAudit(
            created_reason="initial_generation",
            source_sha256="a" * 64,
            result_plan_sha256=plan_payload_sha256(current_plan),
            operations=(
                PlanAuditOperation(
                    operation="initial_generation",
                    actor="system",
                    reason_code="initial_generation",
                    changed_fields=("plan",),
                    field_diffs={
                        "plan": PlanAuditFieldDiff(
                            after_sha256=plan_payload_sha256(current_plan)
                        )
                    },
                    knowledge_binding_action="build",
                ),
            ),
        ),
    }

    with pytest.raises(
        ValidationError,
        match="generator_version must match configuration snapshot",
    ):
        InterviewPlanRevision.model_validate(payload)


def test_revision_store_fails_closed_on_caller_generator_version_drift():
    store = InMemoryInterviewPlanRevisionStore()

    with pytest.raises(
        ValidationError,
        match="generator_version must match configuration snapshot",
    ):
        store.create_initial(
            source_payload=source(),
            plan=plan(),
            retention_policy="test-v1",
            generator_version="different-generator-version",
        )


def test_unvalidated_model_copy_cannot_bypass_configuration_or_plan_hashing():
    policy = _policy()
    valid_configuration = configuration()
    invalid_configuration = valid_configuration.model_copy(
        update={"followup_policy_version": "unfrozen_policy"}
    )
    invalid_plan = plan().model_copy(
        update={"configuration_snapshot": invalid_configuration}
    )

    with pytest.raises(ValidationError):
        plan_configuration_sha256(invalid_configuration)
    with pytest.raises(ValidationError):
        plan_payload_sha256(invalid_plan)
    with pytest.raises(ValidationError):
        evaluate_plan_configuration_policy(invalid_configuration, policy)


def test_policy_semantic_drift_requires_a_new_policy_version_and_hash():
    policy = _policy()
    first_effect = policy.configuration_effects[0].model_copy(
        update={"user_visible_semantics": "Changed without a new version."}
    )
    drifted = policy.model_copy(
        update={
            "configuration_effects": (
                first_effect,
                *policy.configuration_effects[1:],
            )
        }
    )

    with pytest.raises(ValueError, match="hash drift requires a new version"):
        evaluate_plan_configuration_policy(configuration(), drifted)


def test_configuration_changes_generation_only_and_never_the_scoring_rubric():
    policy = _policy()
    before = REPORT_SCORING_RUBRIC_VERSION

    advanced = evaluate_plan_configuration_policy(
        _configuration(
            difficulty="advanced",
            target_duration_minutes=60,
            focus_preset="system_design",
            followup_policy_version="adaptive_v1",
        ),
        policy,
    )

    assert advanced.scoring_rubric_changed is False
    assert policy.scoring_separation.configuration_may_change_rubric is False
    assert policy.scoring_separation.configuration_may_change_passing_threshold is False
    assert REPORT_SCORING_RUBRIC_VERSION == before


def test_v1_parser_compatibility_uses_frozen_defaults_without_provider():
    legacy = _legacy_plan()
    converted = legacy_plan_to_v2(legacy)

    assert converted.schema_version == "interview-plan-v2"
    assert converted.configuration_snapshot.difficulty == "intermediate"
    assert converted.configuration_snapshot.target_duration_minutes == 30
    assert converted.configuration_snapshot.focus_preset == "balanced"
    assert converted.configuration_snapshot.followup_policy_version == "fixed_v1"
    assert converted.configuration_snapshot.max_followups_per_question == 2
    roundtrip = v2_plan_to_legacy(converted)
    assert roundtrip.title == legacy.title
    assert [
        (item.kind, item.prompt, item.focus) for item in roundtrip.questions
    ] == [
        (item.kind, item.prompt, item.focus) for item in legacy.questions
    ]
    assert all(item.id not in {"q1", "q2", "q3"} for item in roundtrip.questions)


def test_t50_does_not_prematurely_freeze_t51_question_ranges():
    policy = _policy()

    assert policy.duration.configuration_values_frozen_in_t50 is True
    assert policy.duration.question_ranges_frozen_in_t50 is False
    assert policy.duration.question_budget_model_owner == "T51"
    assert policy.duration.launch_validation_owner == "T51_T52"
    assert policy.duration.provider_budget_enforcement_owner == "T52"
