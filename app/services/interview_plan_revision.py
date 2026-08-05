from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PlanDifficulty = Literal["foundation", "intermediate", "advanced"]
PlanFocusPreset = Literal[
    "technical_depth", "system_design", "project_review", "balanced"
]
PlanQuestionType = Literal["project", "technical", "system-design", "behavioral"]
PlanQuestionOrigin = Literal["generated", "edited", "regenerated", "custom"]
PlanRevisionSourceKind = Literal[
    "generated", "edited", "regenerated_question", "customized"
]
PlanCreatedReason = Literal[
    "initial_generation",
    "edit_question_text",
    "edit_focus",
    "move_question",
    "delete_question",
    "add_custom_question",
    "regenerate_question",
    "restore_revision",
    "regenerate_all",
]
PlanSourceReferenceType = Literal["family", "draft", "session"]


class ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanSourcePayload(ImmutableModel):
    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    job_tags: tuple[str, ...] = ()

    @field_validator("job_description", "resume_text", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source text must not be blank")
        return _normalize_string(value).strip()

    @field_validator("job_tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("job_tags must be a list or tuple")
        normalized = tuple(
            dict.fromkeys(
                _normalize_string(tag).strip()
                for tag in value
                if isinstance(tag, str) and tag.strip()
            )
        )
        return normalized


class PlanConfigurationSnapshot(ImmutableModel):
    difficulty: PlanDifficulty
    target_duration_minutes: Literal[15, 30, 45, 60]
    focus_preset: PlanFocusPreset
    question_type_budget: dict[str, int] = Field(default_factory=dict)
    expected_followup_budget: int = Field(ge=0)
    max_followups_per_question: Literal[2] = 2
    generator_version: str = Field(min_length=1)
    followup_policy_version: str = Field(min_length=1)

    @field_validator("question_type_budget")
    @classmethod
    def validate_question_type_budget(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {"project", "technical", "system-design", "behavioral"}
        if any(key not in allowed for key in value):
            raise ValueError("question_type_budget contains an unsupported type")
        if any(isinstance(count, bool) or count < 0 for count in value.values()):
            raise ValueError("question_type_budget counts must be non-negative integers")
        return dict(sorted(value.items()))


class InterviewPlanQuestionV2(ImmutableModel):
    question_id: str
    position: int = Field(ge=1)
    question_text: str = Field(min_length=1)
    focus: str = Field(min_length=1)
    question_type: PlanQuestionType
    difficulty: PlanDifficulty
    expected_minutes: int = Field(ge=1, le=60)
    expected_followups: int = Field(ge=0, le=2)
    origin: PlanQuestionOrigin
    replaces_question_id: str | None = None
    knowledge_binding: dict[str, Any] = Field(default_factory=dict)

    @field_validator("question_id", "replaces_question_id")
    @classmethod
    def validate_uuid_fields(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _uuid_text(value, info.field_name)

    @field_validator("question_text", "focus", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("question text and focus must not be blank")
        return _normalize_string(value).strip()

    @model_validator(mode="after")
    def validate_origin(self):
        if self.origin == "regenerated" and self.replaces_question_id is None:
            raise ValueError("regenerated questions require replaces_question_id")
        if self.origin != "regenerated" and self.replaces_question_id is not None:
            raise ValueError("only regenerated questions may replace another question")
        return self


class InterviewPlanV2(ImmutableModel):
    schema_version: Literal["interview-plan-v2"] = "interview-plan-v2"
    title: str = Field(min_length=1)
    configuration_snapshot: PlanConfigurationSnapshot
    questions: tuple[InterviewPlanQuestionV2, ...]

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("title must not be blank")
        return _normalize_string(value).strip()

    @field_validator("questions", mode="before")
    @classmethod
    def normalize_questions(cls, value: object):
        if not isinstance(value, (list, tuple)):
            raise ValueError("questions must be a list or tuple")
        return tuple(value)

    @model_validator(mode="after")
    def validate_plan(self):
        if not 3 <= len(self.questions) <= 5:
            raise ValueError("interview-plan-v2 requires 3 to 5 questions")
        ids = [question.question_id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question_id must be unique")
        positions = [question.position for question in self.questions]
        if len(positions) != len(set(positions)):
            raise ValueError("question position must be unique")
        if sorted(positions) != list(range(1, len(self.questions) + 1)):
            raise ValueError("question positions must be contiguous from 1")
        if list(positions) != sorted(positions):
            raise ValueError("questions must be ordered by position")
        return self


class PlanSourceRecord(ImmutableModel):
    source_id: str
    plan_family_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_payload: PlanSourcePayload | None
    retention_policy: str = Field(min_length=1)
    created_at: datetime
    tombstoned_at: datetime | None = None
    tombstone_reason: str | None = None

    @field_validator("source_id", "plan_family_id")
    @classmethod
    def validate_uuid_fields(cls, value: str, info) -> str:
        return _uuid_text(value, info.field_name)

    @model_validator(mode="after")
    def validate_tombstone(self):
        tombstoned = self.tombstoned_at is not None
        if tombstoned != (self.protected_payload is None):
            raise ValueError("tombstoned source must have no protected payload")
        if tombstoned != (self.tombstone_reason is not None):
            raise ValueError("tombstoned source must have a reason")
        if self.protected_payload is not None:
            expected = source_payload_sha256(self.protected_payload)
            if expected != self.source_sha256:
                raise ValueError("source_sha256 does not match protected payload")
        return self


class PlanSourceReference(ImmutableModel):
    source_id: str
    owner_type: PlanSourceReferenceType
    owner_id: str = Field(min_length=1)
    created_at: datetime

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        return _uuid_text(value, "source_id")


class InterviewPlanRevision(ImmutableModel):
    plan_revision_id: str
    plan_family_id: str
    revision: int = Field(ge=1)
    parent_revision_id: str | None
    source_kind: PlanRevisionSourceKind
    source_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_snapshot: PlanConfigurationSnapshot
    plan: InterviewPlanV2
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str = Field(min_length=1)
    created_at: datetime
    created_reason: PlanCreatedReason

    @field_validator(
        "plan_revision_id", "plan_family_id", "parent_revision_id", "source_id"
    )
    @classmethod
    def validate_uuid_fields(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _uuid_text(value, info.field_name)

    @model_validator(mode="after")
    def validate_revision(self):
        if self.revision == 1 and self.parent_revision_id is not None:
            raise ValueError("initial revision cannot have a parent")
        if self.revision > 1 and self.parent_revision_id is None:
            raise ValueError("non-initial revision requires a parent")
        if self.configuration_snapshot != self.plan.configuration_snapshot:
            raise ValueError("revision configuration must match plan snapshot")
        if plan_payload_sha256(self.plan) != self.plan_sha256:
            raise ValueError("plan_sha256 does not match plan")
        return self


def canonical_json_bytes(payload: Any) -> bytes:
    normalized = _canonicalize(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def source_payload_sha256(payload: PlanSourcePayload | dict[str, Any]) -> str:
    model = (
        payload
        if isinstance(payload, PlanSourcePayload)
        else PlanSourcePayload.model_validate(payload)
    )
    return canonical_sha256(model.model_dump(mode="json"))


def plan_payload_sha256(plan: InterviewPlanV2 | dict[str, Any]) -> str:
    model = plan if isinstance(plan, InterviewPlanV2) else InterviewPlanV2.model_validate(plan)
    return canonical_sha256(model.model_dump(mode="json"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            _normalize_string(str(key)): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON does not permit NaN or infinity")
    return value


def _normalize_string(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _uuid_text(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
