"""Versioned contracts for JIT interview question rendering.

These models deliberately do not contain a final question prompt.  The
published :class:`RenderedQuestionV1` is the only runtime source of the text
shown to a candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


QUESTION_INTENT_SCHEMA_VERSION = "question-intent-v1"
RENDERED_QUESTION_SCHEMA_VERSION = "rendered-question-v1"
AssessmentGoal = Literal[
    "ownership",
    "implementation_depth",
    "failure_mode",
    "recovery",
    "tradeoff",
    "scale",
    "reliability",
    "observability",
    "collaboration",
]
ASSESSMENT_GOAL_ORDER: tuple[AssessmentGoal, ...] = (
    "ownership",
    "implementation_depth",
    "failure_mode",
    "recovery",
    "tradeoff",
    "scale",
    "reliability",
    "observability",
    "collaboration",
)
ASSESSMENT_GOALS = frozenset(ASSESSMENT_GOAL_ORDER)
MAIN_QUESTION_FALLBACK_REASON_CODES = frozenset(
    {
        "empty_output",
        "presentation_prefix",
        "multiple_questions",
        "unsafe_instruction",
        "too_long",
        "invented_claim_marker",
        "intent_focus_missing",
        "context_budget_exceeded",
        "provider_auth_failed",
        "provider_rate_limited",
        "provider_timeout",
        "provider_unavailable",
        "total_timeout",
        "provider_interrupted",
    }
)
MAIN_QUESTION_SAFE_REASON_CODES = frozenset(
    {"generated", *MAIN_QUESTION_FALLBACK_REASON_CODES}
)


QUESTION_PRESENTATION_PREFIX_RE = re.compile(
    r"^\s*(?:问题|主问题|question|q\s*\d*)\s*[:：.)、]",
    re.IGNORECASE,
)
QUESTION_NUMBERED_SUBQUESTION_RE = re.compile(
    r"(?:^|\s)(?:\d+[.)、]|[一二三四五六七八九十]+[、.])\s*"
)
QUESTION_INSTRUCTION_MARKER_RE = re.compile(
    r"(?:忽略(?:以上|之前)|system\s+message|\bdeveloper\b)",
    re.IGNORECASE,
)


class QuestionTextShapeError(ValueError):
    """Raised when published question text violates the domain shape contract."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def validate_rendered_question_text(value: object) -> str:
    """Normalize and validate text before it can become a RenderedQuestion.

    This deliberately contains only provider-independent presentation and
    safety checks. Intent-specific checks remain in the generation service.
    """

    if not isinstance(value, str) or not value.strip():
        raise QuestionTextShapeError("empty_output")
    normalized = " ".join(value.split())
    if QUESTION_PRESENTATION_PREFIX_RE.search(normalized):
        raise QuestionTextShapeError("presentation_prefix")
    if normalized.count("?") + normalized.count("？") > 1:
        raise QuestionTextShapeError("multiple_questions")
    if len(QUESTION_NUMBERED_SUBQUESTION_RE.findall(normalized)) > 1:
        raise QuestionTextShapeError("multiple_questions")
    if QUESTION_INSTRUCTION_MARKER_RE.search(normalized):
        raise QuestionTextShapeError("unsafe_instruction")
    if len(normalized) > 4000:
        raise QuestionTextShapeError("too_long")
    return normalized


class QuestionIntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["question-intent-v1"] = QUESTION_INTENT_SCHEMA_VERSION
    question_id: str = Field(min_length=1, max_length=128)
    position: int = Field(ge=1, le=10)
    kind: Literal["project", "technical", "system-design", "behavioral"]
    focus: str = Field(min_length=1, max_length=1000)
    difficulty: Literal["foundation", "intermediate", "advanced"]
    assessment_goals: tuple[AssessmentGoal, ...] = Field(min_length=1, max_length=4)
    expected_minutes: int = Field(ge=1, le=60)
    expected_followups: int = Field(ge=0, le=2)
    origin: Literal["generated", "custom", "regenerated"] = "generated"
    knowledge_binding: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "position",
        "expected_minutes",
        "expected_followups",
        mode="before",
    )
    @classmethod
    def require_strict_integers(cls, value: object, info) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{info.field_name} must be an integer")
        return value

    @field_validator("question_id", "focus", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("question intent text must not be blank")
        return value.strip()

    @field_validator("assessment_goals", mode="before")
    @classmethod
    def validate_goals(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("assessment_goals must be a list")
        goals = tuple(str(item).strip() for item in value)
        if any(not item for item in goals):
            raise ValueError("assessment_goals must not contain blanks")
        if len(set(goals)) != len(goals):
            raise ValueError("assessment_goals must be unique")
        unsupported = set(goals) - ASSESSMENT_GOALS
        if unsupported:
            raise ValueError(f"unsupported assessment goal: {sorted(unsupported)[0]}")
        return goals

class RenderedQuestionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["rendered-question-v1"] = RENDERED_QUESTION_SCHEMA_VERSION
    question_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    knowledge_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_id: str = Field(min_length=1, max_length=128)
    generation_attempt: int = Field(ge=1, le=2)
    render_mode: Literal["generated", "fallback"]
    fallback_reason_code: str | None = Field(default=None, max_length=128)
    provider_invocation_count: int = Field(ge=0, le=2)
    generation_latency_ms: int = Field(ge=0)
    fallback_used: bool
    safe_reason_code: str = Field(min_length=1, max_length=128)
    created_at: datetime

    @field_validator("text", mode="before")
    @classmethod
    def validate_question_text(cls, value: object) -> str:
        try:
            return validate_rendered_question_text(value)
        except QuestionTextShapeError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_fallback_lineage(self):
        if self.render_mode == "fallback" and not self.fallback_reason_code:
            raise ValueError("fallback rendered question requires a reason code")
        if self.render_mode == "generated" and self.fallback_reason_code is not None:
            raise ValueError("generated rendered question cannot have a fallback reason")
        if (
            self.fallback_reason_code is not None
            and self.fallback_reason_code not in MAIN_QUESTION_FALLBACK_REASON_CODES
        ):
            raise ValueError("fallback_reason_code is not an approved diagnostic code")
        if self.fallback_used != (self.render_mode == "fallback"):
            raise ValueError("fallback_used must match render_mode")
        expected_reason = self.fallback_reason_code or "generated"
        if self.safe_reason_code != expected_reason:
            raise ValueError("safe_reason_code must match the committed result")
        if self.safe_reason_code not in MAIN_QUESTION_SAFE_REASON_CODES:
            raise ValueError("safe_reason_code is not an approved diagnostic code")
        return self


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def question_intent_sha256(intent: QuestionIntentV1 | dict) -> str:
    model = intent if isinstance(intent, QuestionIntentV1) else QuestionIntentV1.model_validate(intent)
    return canonical_sha256(model.model_dump(mode="json"))


def main_question_generation_identity(
    *,
    session_id: str,
    question_id: str,
    intent_sha256: str,
    context_sha256: str,
    knowledge_scope_sha256: str,
    prompt_sha256: str,
    generator_version: str,
) -> str:
    """Return the canonical, explicit identity for a main-question request."""

    fields = {
        "session_id": session_id,
        "question_id": question_id,
        "intent_sha256": intent_sha256,
        "context_sha256": context_sha256,
        "knowledge_scope_sha256": knowledge_scope_sha256,
        "prompt_sha256": prompt_sha256,
        "generator_version": generator_version,
    }
    if any(not isinstance(value, str) or not value for value in fields.values()):
        raise ValueError("generation identity fields must be non-empty strings")
    return canonical_sha256(fields)


__all__ = [
    "ASSESSMENT_GOAL_ORDER",
    "ASSESSMENT_GOALS",
    "MAIN_QUESTION_FALLBACK_REASON_CODES",
    "MAIN_QUESTION_SAFE_REASON_CODES",
    "AssessmentGoal",
    "QuestionIntentV1",
    "QuestionTextShapeError",
    "RenderedQuestionV1",
    "canonical_sha256",
    "main_question_generation_identity",
    "question_intent_sha256",
    "validate_rendered_question_text",
]
