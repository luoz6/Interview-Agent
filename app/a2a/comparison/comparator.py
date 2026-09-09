from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.a2a.contracts.common import DomainArtifact


_TRANSPORT_METADATA_FIELDS = {
    "created_at",
    "context_ref",
}


class ComparisonResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: bool
    differences: list[str] = []


@dataclass(frozen=True)
class DeterministicArtifactComparator:
    """Compare business payloads without transport-only metadata."""

    def compare(
        self,
        local: DomainArtifact,
        remote: DomainArtifact,
    ) -> ComparisonResult:
        if local.artifact_type != remote.artifact_type:
            return ComparisonResult(
                matches=False,
                differences=["artifact_type"],
            )
        local_payload = _normalized_payload(local)
        remote_payload = _normalized_payload(remote)
        differences = _payload_differences(local_payload, remote_payload)
        return ComparisonResult(
            matches=not differences,
            differences=differences,
        )


def _normalized_payload(artifact: DomainArtifact) -> dict[str, Any]:
    return artifact.model_dump(
        mode="json",
        exclude=_TRANSPORT_METADATA_FIELDS,
    )


def _payload_differences(
    left: dict[str, Any],
    right: dict[str, Any],
) -> list[str]:
    keys = sorted(set(left) | set(right))
    differences: list[str] = []
    for key in keys:
        if left.get(key) != right.get(key):
            differences.append(key)
    return differences
