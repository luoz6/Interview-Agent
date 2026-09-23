from __future__ import annotations

from typing import Protocol

from app.domain.agents.artifacts import DomainArtifact
from app.domain.execution_lease import LeaseToken
from app.domain.interview.scheduling import (
    AnswerArtifact,
    ExecutionState,
    InvocationIdentity,
    UserCommand,
)


class ExecutionCommitPort(Protocol):
    """Atomic durable boundary for Scheduler logical effects."""

    def commit_answer(
        self,
        *,
        command: UserCommand,
        artifact: AnswerArtifact,
        state: ExecutionState,
    ) -> None: ...

    def commit_agent_result(
        self,
        *,
        artifact_ref: str,
        artifact: DomainArtifact,
        identity: InvocationIdentity | None,
        lease: LeaseToken | None,
        state: ExecutionState,
    ) -> None: ...


__all__ = ["ExecutionCommitPort"]
