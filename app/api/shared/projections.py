from app.services.interview_plan_budget import assess_interview_plan_budget
from app.services.interview_plan_revision import v2_plan_to_legacy
from app.services.prep import (
    public_interview_plan_payload,
    public_interview_plan_v2_payload,
)


def plan_revision_payload(revision) -> dict:
    """Project one immutable plan revision into the public API contract."""

    public_plan = public_interview_plan_v2_payload(revision.plan)
    payload = {
        "plan_family_id": revision.plan_family_id,
        "plan_revision_id": revision.plan_revision_id,
        "revision": revision.revision,
        "plan_sha256": revision.plan_sha256,
        "audit": revision.audit.model_dump(mode="json"),
        "budget_assessment": assess_interview_plan_budget(
            revision.plan
        ).model_dump(mode="json"),
        "plan": public_plan,
    }
    if revision.plan.schema_version == "interview-plan-v2":
        legacy = public_interview_plan_payload(v2_plan_to_legacy(revision.plan))
        if "prep_context" in legacy:
            public_plan["prep_context"] = legacy["prep_context"]
        else:
            public_plan.pop("prep_context", None)
        payload["legacy_plan"] = legacy
    else:
        # A V3 Intent cannot be represented as a legacy plan without inventing
        # final question wording. Prep clients consume `plan` directly.
        public_plan.pop("prep_context", None)
        payload["legacy_plan"] = None
    return payload


__all__ = ["plan_revision_payload"]
