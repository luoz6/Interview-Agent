from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.agents.artifacts import DomainArtifact


class ArtifactMissing(KeyError):
    pass


class ArtifactPayloadConflict(ValueError):
    pass


@runtime_checkable
class ExecutionArtifactStore(Protocol):
    def put_if_absent(
        self,
        artifact_ref: str,
        artifact: DomainArtifact,
    ) -> DomainArtifact: ...

    def get_required(self, artifact_ref: str) -> DomainArtifact: ...

    def exists(self, artifact_ref: str) -> bool: ...


__all__ = [
    "ArtifactMissing",
    "ArtifactPayloadConflict",
    "ExecutionArtifactStore",
]
