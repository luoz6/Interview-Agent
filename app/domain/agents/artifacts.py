from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DomainArtifact(BaseModel):
    """Transport-neutral immutable artifact contract.

    Transport metadata (for example A2A task/context/message identifiers) is
    intentionally not part of this business payload. Transport adapters may
    carry that metadata beside an artifact, while every runtime can depend on
    this single neutral base contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default="1.0")
    artifact_type: str = Field(min_length=1)
    created_at: str = Field(default_factory=_utc_now_iso)
    context_ref: dict[str, Any] = Field(default_factory=dict)


__all__ = ["DomainArtifact"]
