from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.a2a.contracts.common import DomainArtifact
from app.a2a.contracts.errors import A2AAgentError, A2AError
from app.a2a.observability import AgentTaskLog
from app.a2a.protocol import A2ATask, A2AResult, _utc_now_iso


SkillHandler = Callable[[dict[str, Any], Any | None], DomainArtifact]


class LocalA2AServer:
    """In-process A2A task server.

    A2A-V1 deliberately stays in-process. This server owns the logical task
    lifecycle and error mapping without introducing physical deployment.
    """

    def __init__(self, *, observability: AgentTaskLog | None = None) -> None:
        self._handlers: dict[tuple[str, str], SkillHandler] = {}
        self.observability = observability or AgentTaskLog()

    def register(self, *, agent_id: str, skill: str, handler: SkillHandler) -> None:
        self._handlers[(agent_id, skill)] = handler

    @property
    def registered_skills(self) -> set[tuple[str, str]]:
        return set(self._handlers)

    def get_handler(self, *, agent_id: str, skill: str) -> SkillHandler | None:
        return self._handlers.get((agent_id, skill))

    def submit(self, task: A2ATask, *, execution_context: Any | None = None) -> A2AResult:
        handler = self._handlers.get((task.agent_id, task.skill))
        if handler is None:
            error = A2AError(
                code="unsupported_skill",
                retryable=False,
                terminal=True,
                fallback_allowed=False,
                public_message="Agent skill is not available.",
                internal_reason=f"{task.agent_id}:{task.skill} is not registered",
                observability_code="unsupported_skill",
            )
            result = A2AResult(task=self._with_error(task, error))
            self.observability.record(result.task)
            return result

        working = task.model_copy(
            update={
                "status": "working",
                "attempts": task.attempts + 1,
                "updated_at": _utc_now_iso(),
            }
        )
        try:
            artifact = handler(working.input, execution_context)
        except A2AAgentError as exc:
            result = A2AResult(task=self._with_error(working, exc.to_artifact()))
            self.observability.record(result.task)
            return result
        except Exception as exc:
            result = A2AResult(
                task=self._with_error(
                    working,
                    A2AError(
                        code="unexpected_error",
                        retryable=False,
                        terminal=True,
                        fallback_allowed=True,
                        public_message="Agent execution failed.",
                        internal_reason=str(exc),
                        observability_code="unexpected_error",
                    ),
                )
            )
            self.observability.record(result.task)
            return result
        completed = working.model_copy(
            update={
                "status": "completed",
                "output_artifact": artifact,
                "error": None,
                "updated_at": _utc_now_iso(),
            }
        )
        result = A2AResult(task=completed)
        self.observability.record(result.task)
        return result

    def reject(self, task_id: str, *, reason: str) -> A2AResult:
        task = A2ATask(
            task_id=task_id,
            agent_id="unknown",
            skill="unknown",
            status="rejected",
            input={"reason": reason},
        )
        self.observability.record(task)
        return A2AResult(task=task)

    def cancel(self, task_id: str, *, reason: str) -> A2AResult:
        task = A2ATask(
            task_id=task_id,
            agent_id="unknown",
            skill="unknown",
            status="canceled",
            input={"reason": reason},
        )
        self.observability.record(task)
        return A2AResult(task=task)

    @staticmethod
    def _with_error(task: A2ATask, error: A2AError) -> A2ATask:
        return task.model_copy(
            update={
                "status": "failed",
                "error": error,
                "updated_at": _utc_now_iso(),
            }
        )
