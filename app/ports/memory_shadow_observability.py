from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class MemoryShadowEvidenceSource(Protocol):
    """Read-only source of aggregate Shadow evidence."""

    def load(self) -> Mapping[str, Mapping[str, object]]: ...


@runtime_checkable
class MemoryShadowStatusBuilder(Protocol):
    """Build a status-only projection without changing runtime configuration."""

    def build_status(
        self, evidence: Mapping[str, Mapping[str, object]]
    ) -> dict[str, object]: ...
