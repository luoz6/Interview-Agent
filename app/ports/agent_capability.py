"""Canonical neutral Agent capability lookup boundary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from app.domain.interview.scheduling.capabilities import CapabilityDescriptor


@runtime_checkable
class AgentCapabilityPort(Protocol):
    """Resolve typed Agent capabilities without depending on a transport."""

    def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        """Return the capabilities visible to the current application."""

    def resolve(
        self,
        *,
        agent_id: str,
        skill: str,
        capability_version: str | None = None,
    ) -> CapabilityDescriptor | None:
        """Resolve one capability identity, or return ``None`` if unavailable."""

    def validate_compatibility(
        self,
        *,
        agent_id: str,
        skill: str,
        request_contract_id: str,
        request_contract_version: str,
        input_artifact_types: Iterable[str] = (),
        capability_version: str | None = None,
    ) -> bool:
        """Validate typed request and required-input compatibility."""


__all__ = ["AgentCapabilityPort"]
