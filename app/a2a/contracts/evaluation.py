from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.agents.artifacts import DomainArtifact


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
    evaluation_status: Literal[
        "evaluated",
        "degraded",
        "insufficient_evidence",
    ] = "evaluated"

    @model_validator(mode="after")
    def validate_fake_success_guard(self) -> "EvaluationArtifactPayload":
        if self.evaluation_status == "evaluated" and self.score is None:
            raise ValueError("evaluated artifacts require a score")
        if self.evaluation_status == "insufficient_evidence" and self.score is not None:
            raise ValueError("insufficient evidence artifacts cannot contain a score")
        return self


class EvaluationGapPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    focus: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class EvaluationArtifactV2(DomainArtifact):
    artifact_type: Literal["evaluation-artifact"] = "evaluation-artifact"
    schema_version: Literal["evaluation-artifact-v2"] = "evaluation-artifact-v2"
    question_id: str = Field(min_length=1)
    answer_artifact_ref: str = Field(min_length=1)
    question_artifact_ref: str = Field(min_length=1)
    evaluation_status: Literal["EVALUATED", "DEGRADED"]
    evidence_status: Literal["SUFFICIENT", "INSUFFICIENT", "UNDETERMINED"]
    score: int | None = Field(default=None, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    gap: EvaluationGapPayload | None = None
    evidence_refs: tuple[str, ...] = ()
    summary: str = ""
    evaluation_policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_status_matrix(self) -> "EvaluationArtifactV2":
        if self.evaluation_status == "EVALUATED":
            if self.evidence_status == "SUFFICIENT":
                if self.score is None or self.gap is not None:
                    raise ValueError("sufficient evaluation requires score and no gap")
                return self
            if self.evidence_status == "INSUFFICIENT":
                if self.score is not None or self.gap is None:
                    raise ValueError("insufficient evaluation requires gap and no score")
                return self
            raise ValueError("evaluated artifact cannot be undetermined")
        if self.evidence_status != "UNDETERMINED":
            raise ValueError("degraded artifact must be undetermined")
        if self.gap is not None:
            raise ValueError("degraded artifact cannot declare a gap")
        return self


__all__ = [
    "EvaluationArtifactPayload",
    "EvaluationArtifactV2",
    "EvaluationGapPayload",
]
