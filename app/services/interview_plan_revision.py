from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.interview_plan_budget import (
    MAX_SAFE_MAIN_QUESTION_COUNT,
    MIN_SAFE_MAIN_QUESTION_COUNT,
    allocate_expected_followups,
    allocate_main_answer_minutes,
)
from app.services.interview_plan_audit import PlanRevisionAudit
from app.services.interview_plan_knowledge import (
    binding_from_prep_context,
    parse_question_knowledge_binding,
    revalidate_question_knowledge,
    unbound_question_knowledge,
)


PlanDifficulty = Literal["foundation", "intermediate", "advanced"]
PlanFocusPreset = Literal[
    "technical_depth", "system_design", "project_review", "balanced"
]
PlanQuestionType = Literal["project", "technical", "system-design", "behavioral"]
PlanQuestionOrigin = Literal["generated", "edited", "regenerated", "custom"]
PlanFollowupPolicyVersion = Literal["fixed_v1", "adaptive_v1"]
DEFAULT_PLAN_GENERATOR_VERSION = "plan-generator-v2"
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
    "batch_edit",
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
    question_type_budget: dict[PlanQuestionType, int] = Field(
        default_factory=dict
    )
    expected_followup_budget: int = Field(ge=0)
    max_followups_per_question: Literal[2] = 2
    generator_version: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    followup_policy_version: PlanFollowupPolicyVersion

    @field_validator("question_type_budget", mode="before")
    @classmethod
    def validate_question_type_budget(
        cls,
        value: object,
    ) -> dict[PlanQuestionType, int]:
        if not isinstance(value, dict):
            raise ValueError("question_type_budget must be an object")
        allowed = {"project", "technical", "system-design", "behavioral"}
        if any(key not in allowed for key in value):
            raise ValueError("question_type_budget contains an unsupported type")
        if any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for count in value.values()
        ):
            raise ValueError("question_type_budget counts must be non-negative integers")
        if sum(value.values()) < 1:
            raise ValueError("question_type_budget must request at least one question")
        return dict(sorted(value.items()))

    @field_validator("expected_followup_budget", mode="before")
    @classmethod
    def validate_expected_followup_budget(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected_followup_budget must be an integer")
        return value


def default_plan_configuration() -> PlanConfigurationSnapshot:
    """Return the configured V2 default for new prep requests."""
    return PlanConfigurationSnapshot(
        difficulty="intermediate",
        target_duration_minutes=30,
        focus_preset="balanced",
        question_type_budget={
            "project": 1,
            "technical": 2,
            "system-design": 1,
            "behavioral": 1,
        },
        expected_followup_budget=5,
        generator_version=DEFAULT_PLAN_GENERATOR_VERSION,
        followup_policy_version="fixed_v1",
    )


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
    knowledge_binding: dict[str, Any] = Field(
        default_factory=lambda: unbound_question_knowledge(
            "legacy_no_binding"
        ).model_dump(mode="json")
    )

    @field_validator(
        "position",
        "expected_minutes",
        "expected_followups",
        mode="before",
    )
    @classmethod
    def validate_integer_fields(cls, value: object, info) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{info.field_name} must be an integer")
        return value

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

    @field_validator("knowledge_binding", mode="before")
    @classmethod
    def validate_knowledge_binding(cls, value: object) -> dict[str, Any]:
        return parse_question_knowledge_binding(value).model_dump(mode="json")

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
    prep_context: dict[str, Any] | None = None

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
        if not (
            MIN_SAFE_MAIN_QUESTION_COUNT
            <= len(self.questions)
            <= MAX_SAFE_MAIN_QUESTION_COUNT
        ):
            raise ValueError(
                "interview-plan-v2 requires 1 to 10 questions"
            )
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
    audit: PlanRevisionAudit

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
        if self.generator_version != self.configuration_snapshot.generator_version:
            raise ValueError(
                "revision generator_version must match configuration snapshot"
            )
        if plan_payload_sha256(self.plan) != self.plan_sha256:
            raise ValueError("plan_sha256 does not match plan")
        if self.audit.created_reason != self.created_reason:
            raise ValueError("revision audit created_reason does not match revision")
        if self.audit.source_sha256 != self.source_sha256:
            raise ValueError("revision audit source hash does not match revision")
        if self.audit.result_plan_sha256 != self.plan_sha256:
            raise ValueError("revision audit result hash does not match revision")
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


def plan_configuration_sha256(
    configuration: PlanConfigurationSnapshot | dict[str, Any],
) -> str:
    payload = (
        configuration.model_dump(mode="json")
        if isinstance(configuration, PlanConfigurationSnapshot)
        else configuration
    )
    model = PlanConfigurationSnapshot.model_validate(payload)
    return canonical_sha256(model.model_dump(mode="json"))


def plan_payload_sha256(plan: InterviewPlanV2 | dict[str, Any]) -> str:
    payload = plan.model_dump(mode="json") if isinstance(plan, InterviewPlanV2) else plan
    model = InterviewPlanV2.model_validate(payload)
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


def legacy_plan_to_v2(
    plan: Any,
    *,
    generator_version: str | None = None,
    configuration_snapshot: PlanConfigurationSnapshot | None = None,
) -> InterviewPlanV2:
    """Convert the legacy generated plan at the compatibility boundary.

    The conversion allocates opaque IDs once; subsequent previews and starts
    consume the stored V2 revision instead of calling the LLM.
    """
    if configuration_snapshot is not None:
        config = PlanConfigurationSnapshot.model_validate(
            configuration_snapshot.model_dump(mode="json")
        )
        if (
            generator_version is not None
            and generator_version != config.generator_version
        ):
            raise ValueError(
                "generator_version must match the supplied configuration"
            )
    else:
        effective_generator_version = (
            generator_version or "plan-generator-v2"
        )
        config = PlanConfigurationSnapshot(
            difficulty="intermediate",
            target_duration_minutes=30,
            focus_preset="balanced",
            question_type_budget={
                kind: sum(1 for item in plan.questions if item.kind == kind)
                for kind in {item.kind for item in plan.questions}
            },
            expected_followup_budget=len(plan.questions),
            generator_version=effective_generator_version,
            followup_policy_version="fixed_v1",
        )
    context = (
        deepcopy(plan.prep_context.model_dump(mode="json"))
        if getattr(plan, "prep_context", None) is not None
        else None
    )
    expected_followups = allocate_expected_followups(
        expected_followup_budget=config.expected_followup_budget,
        question_count=len(plan.questions),
        max_followups_per_question=config.max_followups_per_question,
    )
    main_answer_minutes = allocate_main_answer_minutes(
        target_duration_minutes=config.target_duration_minutes,
        expected_followups=expected_followups,
    )
    questions: list[InterviewPlanQuestionV2] = []
    identity_map: dict[str, str] = {}
    for index, item in enumerate(plan.questions, start=1):
        question_id = str(uuid4())
        identity_map[item.id] = question_id
        questions.append(
            InterviewPlanQuestionV2(
                question_id=question_id,
                position=index,
                question_text=item.prompt,
                focus=item.focus,
                question_type=item.kind,
                difficulty=config.difficulty,
                expected_minutes=main_answer_minutes[index - 1],
                expected_followups=expected_followups[index - 1],
                origin="generated",
                knowledge_binding=binding_from_prep_context(
                    context,
                    item.id,
                ).model_dump(mode="json"),
            )
        )
    if context is not None:
        for hint in context.get("question_hints", []):
            temporary_id = hint.get("question_id")
            if temporary_id in identity_map:
                hint["question_id"] = identity_map[temporary_id]
        context.pop("question_bindings", None)
    return synchronize_plan_knowledge_context(InterviewPlanV2(
        title=plan.title,
        configuration_snapshot=config,
        questions=tuple(questions),
        prep_context=context,
    ))


def v2_plan_to_legacy(plan: InterviewPlanV2) -> Any:
    from app.services.prep import InterviewPlan, InterviewQuestion, PrepContext

    plan = synchronize_plan_knowledge_context(plan)
    questions = [
        InterviewQuestion(
            id=item.question_id,
            kind=item.question_type,
            prompt=item.question_text,
            focus=item.focus,
        )
        for item in plan.questions
    ]
    context_payload = deepcopy(plan.prep_context)
    if context_payload is not None:
        context_payload["question_bindings"] = {
            item.question_id: item.knowledge_binding
            for item in plan.questions
        }
    context = (
        PrepContext.model_validate(context_payload)
        if context_payload
        else None
    )
    return InterviewPlan(title=plan.title, questions=questions, prep_context=context)


def synchronize_plan_knowledge_context(plan: InterviewPlanV2) -> InterviewPlanV2:
    context = deepcopy(plan.prep_context)
    normalized_questions: list[InterviewPlanQuestionV2] = []
    for question in plan.questions:
        binding = revalidate_question_knowledge(
            question.knowledge_binding,
            context,
        )
        if question.origin == "custom" and not (
            binding.status == "unbound"
            and binding.reason_code == "custom_question"
        ):
            raise ValueError("custom questions cannot claim knowledge grounding")
        normalized_questions.append(
            question.model_copy(
                update={"knowledge_binding": binding.model_dump(mode="json")}
            )
        )
    if context is None:
        return plan.model_copy(update={"questions": tuple(normalized_questions)})

    existing_hints = {
        item.get("question_id"): item
        for item in context.get("question_hints", [])
        if isinstance(item, dict) and item.get("question_id")
    }
    evidence_titles = {
        item.get("evidence_id"): item.get("title", "")
        for item in context.get("evidence_refs", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    synchronized_hints: list[dict[str, Any]] = []
    for question in normalized_questions:
        binding = parse_question_knowledge_binding(question.knowledge_binding)
        hint = deepcopy(existing_hints.get(question.question_id, {}))
        hint["question_id"] = question.question_id
        if binding.status == "valid":
            hint["evidence_ids"] = list(binding.evidence_ids)
            hint["evidence_titles"] = [
                evidence_titles[evidence_id]
                for evidence_id in binding.evidence_ids
                if evidence_titles.get(evidence_id)
            ]
            hint.setdefault("topic_ids", [])
            hint.setdefault("follow_up_hints", [])
        elif (
            binding.status == "unbound"
            and binding.reason_code == "no_grounded_evidence"
            and hint
        ):
            hint["evidence_titles"] = []
            hint["evidence_ids"] = []
            hint.setdefault("topic_ids", [])
            hint.setdefault("follow_up_hints", [])
        else:
            hint.update(
                {
                    "topic_ids": [],
                    "follow_up_hints": [],
                    "evidence_titles": [],
                    "evidence_ids": [],
                }
            )
        synchronized_hints.append(hint)
    context["question_hints"] = synchronized_hints
    context.pop("question_bindings", None)
    return plan.model_copy(
        update={
            "questions": tuple(normalized_questions),
            "prep_context": context,
        }
    )
