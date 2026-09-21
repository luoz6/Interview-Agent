"""Output artifact compatibility gates for Agent capability dispatch."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling.capabilities import CapabilityDescriptor
from app.domain.interview.scheduling.state import (
    ExecutionArtifactRef,
    ExecutionState,
)


class OutputCompatibilityError(ValueError):
    """Raised when an Agent output cannot be accepted by its capability."""

    def __init__(
        self,
        message: str,
        *,
        expected_type: str,
        actual_type: str | None,
        expected_version: str,
        actual_version: str | None,
    ) -> None:
        super().__init__(message)
        self.code = "output_incompatible"
        self.expected_type = expected_type
        self.actual_type = actual_type
        self.expected_version = expected_version
        self.actual_version = actual_version


def _artifact_identity(
    artifact: DomainArtifact | ExecutionArtifactRef | Mapping[str, Any] | Any,
) -> tuple[str | None, str | None]:
    if isinstance(artifact, Mapping):
        artifact_type = artifact.get("artifact_type")
        artifact_version = artifact.get(
            "artifact_version", artifact.get("schema_version")
        )
    else:
        artifact_type = getattr(artifact, "artifact_type", None)
        artifact_version = getattr(artifact, "artifact_version", None)
        if artifact_version is None:
            artifact_version = getattr(artifact, "schema_version", None)
    return (
        artifact_type if isinstance(artifact_type, str) else None,
        artifact_version if isinstance(artifact_version, str) else None,
    )


def validate_output_compatibility(
    capability: CapabilityDescriptor,
    artifact: DomainArtifact | ExecutionArtifactRef | Mapping[str, Any] | Any,
) -> None:
    """Validate an actual output against its declared capability contract.

    The check is deliberately exact: both artifact type and version must match
    the descriptor.  A missing type/version is incompatible and therefore
    cannot be recorded in scheduler state.
    """

    actual_type, actual_version = _artifact_identity(artifact)
    if (
        actual_type is None
        or actual_version is None
        or not capability.is_output_compatible(
            artifact_type=actual_type,
            artifact_version=actual_version,
        )
    ):
        raise OutputCompatibilityError(
            "Agent output artifact is incompatible with capability: "
            f"expected {capability.output_artifact_type}@"
            f"{capability.output_artifact_version}, got "
            f"{actual_type or '<missing>'}@{actual_version or '<missing>'}",
            expected_type=capability.output_artifact_type,
            actual_type=actual_type,
            expected_version=capability.output_artifact_version,
            actual_version=actual_version,
        )


def record_compatible_artifact(
    state: ExecutionState,
    capability: CapabilityDescriptor,
    artifact: DomainArtifact | ExecutionArtifactRef | Mapping[str, Any] | Any,
    *,
    artifact_ref: str | None = None,
    task_id: str | None = None,
) -> ExecutionState:
    """Validate an output, then append its reference through a CAS transition.

    Compatibility is checked before constructing the next state.  Consequently
    an incompatible output raises without mutating or advancing ``state``.
    """

    validate_output_compatibility(capability, artifact)
    actual_type, actual_version = _artifact_identity(artifact)
    if artifact_ref is None and isinstance(artifact, ExecutionArtifactRef):
        artifact_ref = artifact.artifact_ref
    if not isinstance(artifact_ref, str) or not artifact_ref.strip():
        raise ValueError("artifact_ref is required to record an Agent output")
    ref = ExecutionArtifactRef(
        artifact_ref=artifact_ref,
        artifact_type=actual_type or capability.output_artifact_type,
        artifact_version=actual_version or capability.output_artifact_version,
        task_id=task_id,
    )
    return state.apply_transition(
        expected_revision=state.revision,
        transition_name=f"artifact:{ref.artifact_ref}",
        artifact_refs=state.artifact_refs + (ref,),
    )


# Name reads naturally at scheduler call sites and is kept as an explicit
# alias rather than a second implementation.
append_compatible_artifact = record_compatible_artifact


__all__ = [
    "OutputCompatibilityError",
    "append_compatible_artifact",
    "record_compatible_artifact",
    "validate_output_compatibility",
]
