from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.context_artifacts import ArtifactType
from app.services.config import (
    get_context_compression_evidence_enabled,
    get_context_compression_interview_enabled,
    get_context_compression_prep_enabled,
    get_context_compression_review_enabled,
    get_context_compression_shadow_enabled,
)


CompressionWorkflow = Literal["prep", "interview", "review"]


@dataclass(frozen=True)
class ContextCompressionGates:
    shadow_enabled: bool = False
    prep_enabled: bool = False
    interview_enabled: bool = False
    evidence_enabled: bool = False
    review_enabled: bool = False

    @classmethod
    def from_env(cls) -> "ContextCompressionGates":
        return cls(
            shadow_enabled=get_context_compression_shadow_enabled(),
            prep_enabled=get_context_compression_prep_enabled(),
            interview_enabled=get_context_compression_interview_enabled(),
            evidence_enabled=get_context_compression_evidence_enabled(),
            review_enabled=get_context_compression_review_enabled(),
        )

    def creation_enabled(
        self,
        *,
        workflow: CompressionWorkflow,
    ) -> bool:
        return self.shadow_enabled or self._workflow_enabled(workflow)

    def consumption_enabled(
        self,
        *,
        workflow: CompressionWorkflow,
        artifact_type: ArtifactType,
    ) -> bool:
        if not self._workflow_enabled(workflow):
            return False
        if artifact_type == "evidence_compression":
            return self.evidence_enabled
        return True

    def _workflow_enabled(self, workflow: CompressionWorkflow) -> bool:
        return {
            "prep": self.prep_enabled,
            "interview": self.interview_enabled,
            "review": self.review_enabled,
        }[workflow]
