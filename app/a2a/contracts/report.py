from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.a2a.contracts.common import DomainArtifact


class ReportArtifactPayload(DomainArtifact):
    artifact_type: Literal["report-artifact"] = "report-artifact"
    session_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    dimension_scores: dict[str, int | None] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    action_plan: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_refs: list[str] = Field(default_factory=list)
    report_policy_version: str = Field(min_length=1)
