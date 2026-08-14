from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeEngine(StrEnum):
    LEGACY = "legacy"
    HYBRID_V2 = "hybrid-v2"


class RuntimeFallbackReason(StrEnum):
    CANDIDATE_ENGINE_FAILED = "candidate_engine_failed"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"


class RuntimeEngineExecution(BaseModel):
    """Privacy-safe identity for one explicitly configured retrieval execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_engine: KnowledgeEngine
    effective_engine: KnowledgeEngine
    fallback_reason: RuntimeFallbackReason | None = None
    retrieval_availability: str
    engine_version: str = Field(min_length=1)
    execution_schema_version: str = "runtime-engine-execution-v1"
    migrated_from_legacy_assignment: bool = False

    @model_validator(mode="after")
    def validate_fallback(self):
        if self.fallback_reason is None and self.requested_engine != self.effective_engine:
            raise ValueError("engine change requires a fallback reason")
        if self.fallback_reason is not None and not (
            self.requested_engine == KnowledgeEngine.HYBRID_V2
            and self.effective_engine == KnowledgeEngine.LEGACY
        ):
            raise ValueError("fallback is limited to hybrid-v2 -> legacy")
        return self


class LegacyKnowledgeEngineAssignment(BaseModel):
    """Read-only compatibility model for pre-demo persisted snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    engine: KnowledgeEngine
    assignment_version: str
    bucket: int = Field(ge=0, le=99)
    rollout_percent: int = Field(ge=0, le=100)


def execution_from_legacy_assignment(
    value: LegacyKnowledgeEngineAssignment | dict[str, Any],
) -> RuntimeEngineExecution:
    assignment = (
        value
        if isinstance(value, LegacyKnowledgeEngineAssignment)
        else LegacyKnowledgeEngineAssignment.model_validate(value)
    )
    return RuntimeEngineExecution(
        requested_engine=assignment.engine,
        effective_engine=assignment.engine,
        retrieval_availability="legacy_snapshot",
        engine_version="legacy-assignment-unknown",
        migrated_from_legacy_assignment=True,
    )
