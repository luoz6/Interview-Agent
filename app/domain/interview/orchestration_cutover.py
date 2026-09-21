"""Immutable execution ownership across Scheduler cutover and OLD drain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


OrchestrationPath = Literal["OLD", "NEW"]


class ExecutionPathConflict(RuntimeError):
    """Raised when an execution is claimed by both orchestration paths."""

    def __init__(
        self,
        *,
        execution_id: str,
        existing_path: OrchestrationPath,
        requested_path: OrchestrationPath,
    ) -> None:
        self.execution_id = execution_id
        self.existing_path = existing_path
        self.requested_path = requested_path
        super().__init__(
            f"execution {execution_id!r} is bound to {existing_path}; "
            f"cannot claim {requested_path}"
        )


class ExecutionPathUnbound(RuntimeError):
    """Raised when code attempts to execute before claiming a path."""


class ExecutionPathBinding(BaseModel):
    """Durable, immutable ownership of one execution by one runtime path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["execution-path-binding-v1"] = (
        "execution-path-binding-v1"
    )
    execution_id: str = Field(min_length=1, max_length=256)
    path: OrchestrationPath
    bound_at: datetime

    @field_validator("execution_id")
    @classmethod
    def validate_execution_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("execution_id must be trimmed")
        return value

    @field_validator("bound_at")
    @classmethod
    def validate_bound_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bound_at must be timezone-aware")
        return value


__all__ = [
    "ExecutionPathBinding",
    "ExecutionPathConflict",
    "ExecutionPathUnbound",
    "OrchestrationPath",
]
