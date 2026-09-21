from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class ExecutionTaskDefinition(BaseModel):
    """Immutable definition of work the Scheduler may dispatch.

    Runtime status, attempts, retry counters, and produced artifact references
    intentionally do not belong here; those are ExecutionState concerns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    input_contract: str | None = Field(default=None, min_length=1)
    output_contract: str | None = Field(default=None, min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ExecutionDependencyDefinition(BaseModel):
    """A directed dependency between two task definitions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predecessor_task_id: str = Field(min_length=1)
    successor_task_id: str = Field(min_length=1)
    dependency_type: Literal["completion"] = "completion"

    @model_validator(mode="after")
    def reject_self_dependency(self) -> "ExecutionDependencyDefinition":
        if self.predecessor_task_id == self.successor_task_id:
            raise ValueError("a task cannot depend on itself")
        return self


class ExecutionConstraints(BaseModel):
    """Static limits for one execution definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_scheduler_steps: int | None = Field(default=None, ge=1)
    max_tasks: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    max_wall_time_seconds: int | None = Field(default=None, ge=1)


class ExecutionPlan(BaseModel):
    """Pure execution definition, deliberately separate from runtime state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1)
    interview_plan_ref: str = Field(min_length=1)
    definition_revision: int = Field(
        default=1,
        ge=1,
        validation_alias=AliasChoices("definition_revision", "revision"),
    )
    task_definitions: tuple[ExecutionTaskDefinition, ...] = Field(
        default=(),
        validation_alias=AliasChoices("task_definitions", "tasks"),
    )
    dependency_definitions: tuple[ExecutionDependencyDefinition, ...] = Field(
        default=(),
        validation_alias=AliasChoices("dependency_definitions", "dependencies"),
    )
    execution_constraints: ExecutionConstraints = Field(
        default_factory=ExecutionConstraints,
        validation_alias=AliasChoices("execution_constraints", "constraints"),
    )

    @property
    def revision(self) -> int:
        """Compatibility spelling for callers using the short revision name."""

        return self.definition_revision

    @property
    def tasks(self) -> tuple[ExecutionTaskDefinition, ...]:
        return self.task_definitions

    @property
    def dependencies(self) -> tuple[ExecutionDependencyDefinition, ...]:
        return self.dependency_definitions

    @property
    def constraints(self) -> ExecutionConstraints:
        return self.execution_constraints

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        task_ids = [task.task_id for task in self.task_definitions]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique")
        task_id_set = set(task_ids)
        adjacency: dict[str, set[str]] = defaultdict(set)
        indegree = {task_id: 0 for task_id in task_ids}
        for dependency in self.dependency_definitions:
            if dependency.predecessor_task_id not in task_id_set:
                raise ValueError(
                    "dependency predecessor must reference a defined task"
                )
            if dependency.successor_task_id not in task_id_set:
                raise ValueError(
                    "dependency successor must reference a defined task"
                )
            if dependency.successor_task_id not in adjacency[
                dependency.predecessor_task_id
            ]:
                adjacency[dependency.predecessor_task_id].add(
                    dependency.successor_task_id
                )
                indegree[dependency.successor_task_id] += 1

        if self.execution_constraints.max_tasks is not None and len(task_ids) > self.execution_constraints.max_tasks:
            raise ValueError("task definitions exceed max_tasks")

        ready = deque(task_id for task_id, degree in indegree.items() if degree == 0)
        visited = 0
        while ready:
            task_id = ready.popleft()
            visited += 1
            for successor in adjacency[task_id]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
        if visited != len(task_ids):
            raise ValueError("execution task dependencies must be acyclic")
        return self


__all__ = [
    "ExecutionConstraints",
    "ExecutionDependencyDefinition",
    "ExecutionPlan",
    "ExecutionTaskDefinition",
]
