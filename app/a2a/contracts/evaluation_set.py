from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.a2a.contracts.common import DomainArtifact
from app.a2a.contracts.evaluation import EvaluationArtifactPayload


class EvaluationArtifactSetPayload(DomainArtifact):
    artifact_type: Literal["evaluation-artifact-set"] = "evaluation-artifact-set"
    evaluations: list[EvaluationArtifactPayload] = Field(default_factory=list)
