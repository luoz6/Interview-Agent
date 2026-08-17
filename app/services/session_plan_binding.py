from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.interview_plan_revision import (
    InterviewPlanRevision,
    InterviewPlanV2,
    canonical_sha256,
    plan_payload_sha256,
)
from app.services.prep import InterviewPlan


class SessionPlanBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_origin: Literal["plan_revision", "legacy_session_snapshot"]
    plan_revision_id: str | None = None
    plan_family_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_snapshot: dict[str, Any] | None = None
    plan_snapshot: dict[str, Any]
    principal_memory_mode: Literal["inherit", "ignore"] = "inherit"
    owner_principal_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]{1,128}$",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_binding(self):
        revision_fields = (
            self.plan_revision_id,
            self.plan_family_id,
            self.revision,
            self.configuration_snapshot,
        )
        if self.plan_origin == "plan_revision":
            if any(value is None for value in revision_fields):
                raise ValueError("plan revision session binding is incomplete")
            plan = InterviewPlanV2.model_validate(self.plan_snapshot)
            if plan_payload_sha256(plan) != self.plan_sha256:
                raise ValueError("session plan snapshot hash does not match revision")
            if plan.configuration_snapshot.model_dump(mode="json") != self.configuration_snapshot:
                raise ValueError("session configuration snapshot does not match plan")
        else:
            if any(value is not None for value in revision_fields):
                raise ValueError("legacy session binding cannot claim a plan revision")
            legacy_plan = InterviewPlan.model_validate(self.plan_snapshot)
            if canonical_sha256(legacy_plan.model_dump(mode="json")) != self.plan_sha256:
                raise ValueError("legacy session plan snapshot hash does not match")
        return self


def session_plan_binding_from_revision(
    revision: InterviewPlanRevision,
    *,
    principal_memory_mode: Literal["inherit", "ignore"] = "inherit",
    owner_principal_id: str | None = None,
) -> SessionPlanBinding:
    return SessionPlanBinding(
        plan_origin="plan_revision",
        plan_revision_id=revision.plan_revision_id,
        plan_family_id=revision.plan_family_id,
        revision=revision.revision,
        plan_sha256=revision.plan_sha256,
        configuration_snapshot=revision.configuration_snapshot.model_dump(mode="json"),
        plan_snapshot=revision.plan.model_dump(mode="json"),
        principal_memory_mode=principal_memory_mode,
        owner_principal_id=owner_principal_id,
    )


def legacy_session_plan_binding(plan: InterviewPlan | dict[str, Any]) -> SessionPlanBinding:
    snapshot = (
        plan.model_dump(mode="json") if isinstance(plan, InterviewPlan) else dict(plan)
    )
    return SessionPlanBinding(
        plan_origin="legacy_session_snapshot",
        plan_sha256=canonical_sha256(snapshot),
        plan_snapshot=snapshot,
    )


def session_plan_binding_from_state(state: dict[str, Any]) -> SessionPlanBinding:
    return SessionPlanBinding.model_validate(
        {
            "plan_origin": state["plan_origin"],
            "plan_revision_id": state.get("plan_revision_id"),
            "plan_family_id": state.get("plan_family_id"),
            "revision": state.get("revision"),
            "plan_sha256": state["plan_sha256"],
            "configuration_snapshot": state.get("configuration_snapshot"),
            "plan_snapshot": state["plan_snapshot"],
            "principal_memory_mode": state.get("principal_memory_mode", "inherit"),
            "owner_principal_id": state.get("owner_principal_id"),
        }
    )
