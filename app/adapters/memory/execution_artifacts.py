from __future__ import annotations

from threading import RLock

from app.domain.agents.artifacts import DomainArtifact
from app.ports.execution_artifacts import (
    ArtifactMissing,
    ArtifactPayloadConflict,
)


class InMemoryExecutionArtifactStore:
    def __init__(self) -> None:
        self._artifacts: dict[str, DomainArtifact] = {}
        self._lock = RLock()

    def put_if_absent(
        self,
        artifact_ref: str,
        artifact: DomainArtifact,
    ) -> DomainArtifact:
        embedded_ref = getattr(artifact, "artifact_ref", artifact_ref)
        if embedded_ref != artifact_ref:
            raise ArtifactPayloadConflict(artifact_ref)
        with self._lock:
            existing = self._artifacts.get(artifact_ref)
            if existing is not None:
                if existing != artifact:
                    raise ArtifactPayloadConflict(artifact_ref)
                return existing
            self._artifacts[artifact_ref] = artifact
            return artifact

    def get_required(self, artifact_ref: str) -> DomainArtifact:
        with self._lock:
            try:
                return self._artifacts[artifact_ref]
            except KeyError as exc:
                raise ArtifactMissing(artifact_ref) from exc

    def exists(self, artifact_ref: str) -> bool:
        with self._lock:
            return artifact_ref in self._artifacts


__all__ = ["InMemoryExecutionArtifactStore"]
