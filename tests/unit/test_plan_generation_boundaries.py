import app.domain.interview.plan_generation as canonical
import app.runtime.interview_prep as legacy


def test_legacy_plan_generation_exports_preserve_object_identity():
    names = (
        "enforce_generated_intent_plan",
        "enforce_generated_interview_plan",
        "enforce_generated_interview_question_quality",
        "interview_plan_from_intent_draft",
        "validate_generation_configuration",
        "validate_launchable_interview_plan",
    )

    assert all(getattr(legacy, name) is getattr(canonical, name) for name in names)
