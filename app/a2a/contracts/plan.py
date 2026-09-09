from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.a2a.contracts.common import DomainArtifact


class InterviewPlanArtifactPayload(DomainArtifact):
    artifact_type: Literal["interview-plan-artifact"] = "interview-plan-artifact"
    plan_payload: dict[str, Any] = Field(default_factory=dict)
