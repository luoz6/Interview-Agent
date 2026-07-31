from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


QUESTION_MEMORY_TAXONOMY_VERSION = "question-memory-taxonomy-v1"
QUESTION_MEMORY_TAXONOMY = frozenset(
    {
        "api_design",
        "cache_consistency",
        "distributed_systems",
        "failure_handling",
        "idempotency",
        "missing_boundary",
        "missing_tradeoff",
        "observability",
        "performance",
        "reliability",
        "security",
        "system_design",
        "testing",
    }
)


class QuestionMemoryIndexEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    question_id_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    focus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    focus_tags: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    skill_tag_sha256: list[str] = Field(default_factory=list)
    unresolved_topic_codes: list[str] = Field(default_factory=list)
    unresolved_topic_sha256: list[str] = Field(default_factory=list)
    artifact_ref: str = Field(
        pattern=r"^context-artifact-ref:[A-Za-z0-9-]{1,128}$"
    )
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_type: Literal["question_memory"] = "question_memory"
    policy_version: str = Field(min_length=1, max_length=128)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_message_count: int = Field(ge=1)
    source_max_sequence_no: int = Field(ge=1)
    taxonomy_version: Literal["question-memory-taxonomy-v1"] = (
        QUESTION_MEMORY_TAXONOMY_VERSION
    )
    status: Literal["active", "superseded", "deleted"] = "active"
    supersedes_artifact_ref: str | None = Field(
        default=None,
        pattern=r"^context-artifact-ref:[A-Za-z0-9-]{1,128}$",
    )
    created_at: datetime
    superseded_at: datetime | None = None
    deleted_at: datetime | None = None

    @field_validator(
        "focus_tags",
        "skill_tags",
        "unresolved_topic_codes",
    )
    @classmethod
    def validate_taxonomy(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("question memory taxonomy values must be unique")
        if any(value not in QUESTION_MEMORY_TAXONOMY for value in values):
            raise ValueError("question memory taxonomy contains free text")
        return values

    @model_validator(mode="after")
    def validate_integrity_fields(self):
        if self.question_id_sha256 != sha256(
            self.question_id.encode("utf-8")
        ).hexdigest():
            raise ValueError("question_id_sha256 does not match question_id")
        expected_skill = [sha256(value.encode("utf-8")).hexdigest() for value in self.skill_tags]
        expected_topics = [
            sha256(value.encode("utf-8")).hexdigest()
            for value in self.unresolved_topic_codes
        ]
        if self.skill_tag_sha256 != expected_skill:
            raise ValueError("skill tag digests do not match taxonomy values")
        if self.unresolved_topic_sha256 != expected_topics:
            raise ValueError("unresolved topic digests do not match taxonomy values")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.status == "active" and (
            self.superseded_at is not None or self.deleted_at is not None
        ):
            raise ValueError("active question memory entry has terminal timestamps")
        return self
