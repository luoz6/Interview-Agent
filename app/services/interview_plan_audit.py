from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlanAuditFieldDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    before_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    after_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_a_change(self):
        if self.before_sha256 == self.after_sha256:
            raise ValueError("audit field diff must describe a change")
        return self


class PlanAuditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    actor: Literal["system", "user", "provider"]
    source_question_id: str | None = None
    result_question_id: str | None = None
    target_revision_id: str | None = None
    reason_code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    changed_fields: tuple[str, ...] = ()
    field_diffs: dict[str, PlanAuditFieldDiff] = Field(default_factory=dict)
    knowledge_binding_action: Literal[
        "build",
        "preserve",
        "invalidate",
        "remove",
        "unbound",
        "rebuild",
        "restore",
        "rebuild_all",
        "none",
    ] = "none"
    knowledge_binding_status: Literal["valid", "unbound", "invalidated"] | None = None
    knowledge_binding_reason_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )

    @field_validator(
        "source_question_id",
        "result_question_id",
        "target_revision_id",
    )
    @classmethod
    def validate_uuid_metadata(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{info.field_name} must be a UUID") from exc

    @field_validator("changed_fields", mode="before")
    @classmethod
    def normalize_changed_fields(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("changed_fields must be a list or tuple")
        normalized = tuple(sorted(dict.fromkeys(str(item) for item in value)))
        if any(
            not item
            or not item[0].isalpha()
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in item
            )
            for item in normalized
        ):
            raise ValueError("changed_fields contain an unsafe field name")
        return normalized

    @model_validator(mode="after")
    def validate_diff_fields(self):
        if set(self.changed_fields) != set(self.field_diffs):
            raise ValueError("changed_fields must exactly match field_diffs")
        if (self.knowledge_binding_status is None) != (
            self.knowledge_binding_reason_code is None
        ):
            raise ValueError(
                "knowledge binding status and reason must be supplied together"
            )
        return self


class PlanRevisionAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["plan-revision-audit-v1"] = "plan-revision-audit-v1"
    created_reason: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_plan_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    result_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_diff: dict[str, PlanAuditFieldDiff] = Field(default_factory=dict)
    operations: tuple[PlanAuditOperation, ...] = Field(min_length=1)
