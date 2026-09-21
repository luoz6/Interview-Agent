"""Backward-compatible A2A import for the neutral artifact contract.

The contract is owned by ``app.domain.agents.artifacts``. This module is an
adapter-level re-export so existing A2A clients do not need a flag-day import
change and, importantly, does not define a second artifact base class.
"""

from app.domain.agents.artifacts import DomainArtifact

__all__ = ["DomainArtifact"]
