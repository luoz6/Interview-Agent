from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.a2a.contracts.common import DomainArtifact


class GroundingArtifactPayload(DomainArtifact):
    artifact_type: Literal["grounding-artifact"] = "grounding-artifact"
    scope: dict[str, Any] = Field(default_factory=dict)
    role_profile_ref: str | None = None
    query_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    knowledge_units: list[dict[str, Any]] = Field(default_factory=list)
    grounding_status: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    trace_ref: str | None = None
