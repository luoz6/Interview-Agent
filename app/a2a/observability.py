from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.a2a.protocol import A2ATask


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AgentTaskObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    agent_id: str
    skill: str
    status: str
    attempts: int
    created_at: str
    updated_at: str
    output_artifact_type: str | None = None
    error_code: str | None = None
    transport: str = "a2a"


class AgentTaskLog:
    def __init__(self) -> None:
        self._items: list[AgentTaskObservation] = []

    def record(self, task: A2ATask) -> None:
        self._items.append(
            AgentTaskObservation(
                task_id=task.task_id,
                agent_id=task.agent_id,
                skill=task.skill,
                status=task.status,
                attempts=task.attempts,
                created_at=task.created_at,
                updated_at=task.updated_at,
                output_artifact_type=(
                    task.output_artifact.artifact_type
                    if task.output_artifact is not None
                    else None
                ),
                error_code=task.error.code if task.error is not None else None,
            )
        )

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self._items]
