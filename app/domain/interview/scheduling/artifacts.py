from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field

from app.domain.agents.artifacts import DomainArtifact


AnswerKind = Literal["MAIN", "FOLLOWUP"]


def answer_artifact_ref(execution_id: str, command_id: str) -> str:
    """Return the frozen deterministic identity for one accepted answer."""

    identity = json.dumps(
        [execution_id, command_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"answer/sha256:{hashlib.sha256(identity).hexdigest()}"


class AnswerArtifact(DomainArtifact):
    artifact_type: Literal["answer-artifact"] = "answer-artifact"
    schema_version: Literal["answer-artifact-v1"] = "answer-artifact-v1"
    artifact_ref: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    source_task_id: str = Field(min_length=1)
    answer_kind: AnswerKind
    answer_text: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    wait_id: str = Field(min_length=1)
    submitted_revision: int = Field(ge=0)
    submitted_at: str = Field(min_length=1)


class StoredExecutionArtifact(DomainArtifact):
    """Lossless persisted form for Agent artifact schemas owned elsewhere."""

    model_config = ConfigDict(frozen=True, extra="allow")


def parse_execution_artifact(payload: dict) -> DomainArtifact:
    if payload.get("artifact_type") == "answer-artifact":
        return AnswerArtifact.model_validate(payload)
    return StoredExecutionArtifact.model_validate(payload)


__all__ = [
    "AnswerArtifact",
    "AnswerKind",
    "StoredExecutionArtifact",
    "answer_artifact_ref",
    "parse_execution_artifact",
]
