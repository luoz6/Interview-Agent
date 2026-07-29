from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.services.context_artifacts import (
    ArtifactPurpose,
    ContextArtifactClaim,
    ContextArtifactCleanupPolicy,
    ContextArtifactCleanupResult,
    ContextArtifactIdentity,
    ContextArtifactRecord,
    ContextArtifactRef,
    OwnerType,
)


@runtime_checkable
class ContextArtifactStore(Protocol):
    def claim(
        self,
        identity: ContextArtifactIdentity,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ContextArtifactClaim:
        ...

    def heartbeat(
        self,
        claim: ContextArtifactClaim,
        *,
        lease_seconds: int,
    ) -> bool:
        ...

    def complete(
        self,
        claim: ContextArtifactClaim,
        payload: dict[str, Any],
    ) -> ContextArtifactRecord:
        ...

    def fail(
        self,
        claim: ContextArtifactClaim,
        *,
        error_code: str,
    ) -> None:
        ...

    def get_terminal_by_key(
        self,
        artifact_key: str,
    ) -> ContextArtifactRecord | None:
        ...

    def create_owner_ref(
        self,
        record: ContextArtifactRecord,
        *,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        retain_until: datetime | None = None,
    ) -> ContextArtifactRef:
        ...

    def load_ref(
        self,
        ref: ContextArtifactRef,
        *,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        expected_identity: ContextArtifactIdentity,
    ) -> ContextArtifactRecord:
        ...

    def delete_owner_refs(
        self,
        *,
        owner_type: OwnerType,
        owner_key: str,
    ) -> int:
        ...

    def cleanup(
        self,
        policy: ContextArtifactCleanupPolicy,
    ) -> ContextArtifactCleanupResult:
        ...
