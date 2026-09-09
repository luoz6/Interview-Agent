from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.a2a.contracts.common import DomainArtifact


class FollowupArtifactPayload(DomainArtifact):
    artifact_type: Literal["followup-artifact"] = "followup-artifact"
    question_id: str = Field(min_length=1)
    gap_id: str | None = None
    followup_text: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    focus: str = ""
    policy_version: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
