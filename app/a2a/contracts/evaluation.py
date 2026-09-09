from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.a2a.contracts.common import DomainArtifact


class EvaluationArtifactPayload(DomainArtifact):
    artifact_type: Literal["evaluation-artifact"] = "evaluation-artifact"
    question_id: str = Field(min_length=1)
    score: int | None = Field(default=None, ge=0, le=100)
    dimensions: dict[str, int | None] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    gap: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evaluation_policy_version: str = Field(min_length=1)
