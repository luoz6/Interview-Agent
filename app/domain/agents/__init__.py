"""Neutral agent-domain contracts shared by transports and runtimes."""

from app.domain.agents.artifacts import DomainArtifact
from app.domain.agents.errors import AgentError, AgentFailure

__all__ = ["AgentError", "AgentFailure", "DomainArtifact"]
