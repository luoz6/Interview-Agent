from app.services.interview_plan_budget import assess_interview_plan_budget
from app.services.interview_plan_revision import v2_plan_to_legacy
from app.services.prep import (
    public_interview_plan_payload,
    public_interview_plan_v2_payload,
)


def plan_revision_payload(revision) -> dict:
    """Project one immutable plan revision into the public API contract."""

    legacy = public_interview_plan_payload(v2_plan_to_legacy(revision.plan))
    public_plan = public_interview_plan_v2_payload(revision.plan)
    if "prep_context" in legacy:
        public_plan["prep_context"] = legacy["prep_context"]
    else:
        public_plan.pop("prep_context", None)
    return {
        "plan_family_id": revision.plan_family_id,
        "plan_revision_id": revision.plan_revision_id,
        "revision": revision.revision,
        "plan_sha256": revision.plan_sha256,
        "audit": revision.audit.model_dump(mode="json"),
        "budget_assessment": assess_interview_plan_budget(
            revision.plan
        ).model_dump(mode="json"),
        "plan": public_plan,
        "legacy_plan": legacy,
    }


__all__ = ["plan_revision_payload"]
