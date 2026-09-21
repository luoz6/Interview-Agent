from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.agents.artifacts import DomainArtifact


class MainQuestionArtifactPayload(DomainArtifact):
    """Neutral artifact emitted when a main interview question is ready."""

    artifact_type: Literal["main-question-artifact"] = "main-question-artifact"
    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    render_mode: Literal["fixed", "generated", "fallback"] = "generated"
    reason_code: str = Field(default="generated", min_length=1)


__all__ = ["MainQuestionArtifactPayload"]
