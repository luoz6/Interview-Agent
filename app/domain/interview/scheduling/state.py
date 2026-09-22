from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.domain.interview.scheduling.waits import WaitHandle
from app.domain.interview.scheduling.plan import ExecutionTaskDefinition


TaskRuntimeStatus = Literal[
    "PENDING",
    "READY",
    "RUNNING",
    "WAITING",
    "COMPLETED",
    "FAILED",
    "SKIPPED",
    "CANCELED",
]
ExecutionStatus = Literal[
    "PENDING",
    "RUNNING",
    "WAITING",
    "COMPLETED",
    "FAILED",
    "CANCELED",
]


class ExecutionStateConflict(RuntimeError):
    """Raised when a transition uses a stale execution revision."""

    def __init__(self, *, expected_revision: int, actual_revision: int) -> None:
        super().__init__(
            "execution state revision conflict: "
            f"expected={expected_revision}, actual={actual_revision}"
        )
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class InvalidTaskTransition(ValueError):
    """Raised when a task status change is outside the frozen state machine."""

    def __init__(self, *, task_id: str, source: str, target: str) -> None:
        super().__init__(
            f"illegal task transition for {task_id}: {source} -> {target}"
        )
        self.task_id = task_id
        self.source = source
        self.target = target


class TaskRuntimeState(BaseModel):
    """Mutable task facts owned by the ExecutionState aggregate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    status: TaskRuntimeStatus = "PENDING"
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    reason_code: str | None = Field(default=None, min_length=1)


_LEGAL_TASK_TRANSITIONS: dict[TaskRuntimeStatus, frozenset[TaskRuntimeStatus]] = {
    "PENDING": frozenset({"READY", "SKIPPED", "CANCELED"}),
    "READY": frozenset({"RUNNING", "SKIPPED", "CANCELED"}),
    "RUNNING": frozenset({"COMPLETED", "FAILED", "WAITING", "CANCELED"}),
    "WAITING": frozenset({"READY", "CANCELED"}),
    "FAILED": frozenset({"READY", "CANCELED"}),
    "COMPLETED": frozenset(),
    "SKIPPED": frozenset(),
    "CANCELED": frozenset(),
}


def transition_task_state(
    current: TaskRuntimeState,
    target_status: TaskRuntimeStatus,
    *,
    reason_code: str | None = None,
) -> TaskRuntimeState:
    """Apply one legal task transition and return a new runtime state."""

    if target_status not in _LEGAL_TASK_TRANSITIONS[current.status]:
        raise InvalidTaskTransition(
            task_id=current.task_id,
            source=current.status,
            target=target_status,
        )
    if (
        current.status == "FAILED"
        and current.attempt >= current.max_attempts
    ):
        raise ValueError(
            f"task {current.task_id} exceeded max_attempts={current.max_attempts}"
        )
    attempt = current.attempt
    if target_status == "RUNNING":
        attempt += 1
        if attempt > current.max_attempts:
            raise ValueError(
                f"task {current.task_id} exceeded max_attempts={current.max_attempts}"
            )
    if target_status == "FAILED" and reason_code is None:
        reason_code = "task_failed"
    if target_status in {"READY", "COMPLETED", "SKIPPED", "CANCELED"}:
        reason_code = None if target_status != "SKIPPED" else reason_code
    return current.model_validate(
        {
            **current.model_dump(mode="python"),
            "status": target_status,
            "attempt": attempt,
            "reason_code": reason_code,
        }
    )


class ExecutionArtifactRef(BaseModel):
    """Reference to an artifact produced or consumed during execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_ref: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    artifact_version: str = Field(
        default="1.0",
        min_length=1,
        validation_alias=AliasChoices("artifact_version", "schema_version"),
    )
    task_id: str | None = Field(default=None, min_length=1)

    @property
    def schema_version(self) -> str:
        """Compatibility spelling used by concrete DomainArtifact payloads."""

        return self.artifact_version


class ExecutionState(BaseModel):
    """The single mutable runtime truth for one execution.

    This aggregate is immutable at the Python object boundary. Callers obtain
    a new state only through ``apply_transition``/``cas_update``; every
    accepted transition increments ``revision`` exactly once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    task_states: tuple[TaskRuntimeState, ...] = ()
    task_attempts: dict[str, int] = Field(default_factory=dict)
    dynamic_task_definitions: tuple[ExecutionTaskDefinition, ...] = ()
    artifact_refs: tuple[ExecutionArtifactRef, ...] = ()
    current_wait_handle: WaitHandle | None = None
    latest_observation: dict[str, Any] | None = None
    scheduler_step_count: int = Field(default=0, ge=0)
    followups_total_used: int = Field(default=0, ge=0)
    followups_by_question: dict[str, int] = Field(default_factory=dict)
    replans_used: int = Field(default=0, ge=0)
    unresolved_gaps: tuple[dict[str, Any], ...] = ()
    execution_status: ExecutionStatus = "PENDING"

    @model_validator(mode="after")
    def validate_task_facts(self) -> "ExecutionState":
        task_ids = [task.task_id for task in self.task_states]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task runtime task_id values must be unique")
        dynamic_ids = [task.task_id for task in self.dynamic_task_definitions]
        if len(dynamic_ids) != len(set(dynamic_ids)):
            raise ValueError("dynamic task_id values must be unique")
        for task_id, attempt in self.task_attempts.items():
            if not task_id.strip():
                raise ValueError("task attempt keys must be non-empty")
            if isinstance(attempt, bool) or attempt < 0:
                raise ValueError("task attempts must be non-negative integers")
        followups = {
            task.task_id: task
            for task in self.dynamic_task_definitions
            if task.task_id.startswith("followup:")
        }
        evaluations = {
            task.task_id: task
            for task in self.dynamic_task_definitions
            if task.task_id.startswith("evaluate-followup:")
        }
        expected_evaluations = {
            f"evaluate-{task_id}" for task_id in followups
        }
        question_counts: dict[str, int] = {}
        for task_id in followups:
            parts = task_id.split(":")
            if len(parts) != 3 or not parts[1] or not parts[2].isdigit():
                raise ValueError("follow-up pair task identity is invalid")
            question_counts[parts[1]] = question_counts.get(parts[1], 0) + 1
        runtime_ids = set(task_ids)
        pair_ids = set(followups) | set(evaluations)
        if (
            set(evaluations) != expected_evaluations
            or not pair_ids <= runtime_ids
            or self.followups_total_used != len(followups)
            or self.followups_by_question != question_counts
            or self.replans_used != len(followups)
        ):
            raise ValueError("follow-up pair budget invariant violated")
        return self

    def task_state(self, task_id: str) -> TaskRuntimeState | None:
        return next(
            (task for task in self.task_states if task.task_id == task_id),
            None,
        )

    def pending_task_ids(self) -> tuple[str, ...]:
        """Derived projection; not an independently stored fact source."""

        return tuple(
            task.task_id
            for task in self.task_states
            if task.status in {"PENDING", "READY", "RUNNING", "WAITING"}
        )

    def apply_transition(
        self,
        *,
        expected_revision: int,
        transition_name: str,
        **changes: Any,
    ) -> "ExecutionState":
        if "task_states" in changes or "task_attempts" in changes:
            raise ValueError(
                "task status changes must use transition_task"
            )
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name=transition_name,
            **changes,
        )

    def register_followup_pair(
        self,
        *,
        expected_revision: int,
        question_id: str,
        followup: ExecutionTaskDefinition,
        evaluation: ExecutionTaskDefinition,
    ) -> "ExecutionState":
        existing_definitions = {
            task.task_id: task for task in self.dynamic_task_definitions
        }
        existing_runtime = {
            task.task_id: task for task in self.task_states
        }
        present = (
            followup.task_id in existing_definitions,
            evaluation.task_id in existing_definitions,
            followup.task_id in existing_runtime,
            evaluation.task_id in existing_runtime,
        )
        if all(present):
            if (
                existing_definitions[followup.task_id] != followup
                or existing_definitions[evaluation.task_id] != evaluation
            ):
                raise RuntimeError("follow-up pair identity payload conflict")
            return self
        if any(present):
            raise RuntimeError("partial follow-up pair invariant violation")
        counts = dict(self.followups_by_question)
        counts[question_id] = counts.get(question_id, 0) + 1
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name=f"followup-pair:{question_id}:{counts[question_id]}",
            dynamic_task_definitions=(
                self.dynamic_task_definitions + (followup, evaluation)
            ),
            task_states=self.task_states
            + (
                TaskRuntimeState(task_id=followup.task_id),
                TaskRuntimeState(task_id=evaluation.task_id),
            ),
            followups_total_used=self.followups_total_used + 1,
            followups_by_question=counts,
            replans_used=self.replans_used + 1,
        )

    def transition_task(
        self,
        *,
        expected_revision: int,
        task_id: str,
        target_status: TaskRuntimeStatus,
        reason_code: str | None = None,
    ) -> "ExecutionState":
        current = self.task_state(task_id)
        if current is None:
            raise KeyError(f"unknown task_id: {task_id}")
        updated_task = transition_task_state(
            current,
            target_status,
            reason_code=reason_code,
        )
        updated_tasks = tuple(
            updated_task if task.task_id == task_id else task
            for task in self.task_states
        )
        updated_attempts = dict(self.task_attempts)
        updated_attempts[task_id] = updated_task.attempt
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name=f"task:{task_id}:{target_status}",
            task_states=updated_tasks,
            task_attempts=updated_attempts,
        )

    def add_dynamic_task(
        self,
        *,
        expected_revision: int,
        task: ExecutionTaskDefinition,
    ) -> "ExecutionState":
        """Atomically register one new dynamic task in definition and runtime facts."""

        if self.task_state(task.task_id) is not None or any(
            item.task_id == task.task_id for item in self.dynamic_task_definitions
        ):
            raise ValueError(f"dynamic task already exists: {task.task_id}")
        if any(item.task_id == task.task_id for item in self.task_states):
            raise ValueError(f"dynamic task runtime already exists: {task.task_id}")
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name=f"add_dynamic_task:{task.task_id}",
            task_states=self.task_states + (TaskRuntimeState(task_id=task.task_id),),
            dynamic_task_definitions=self.dynamic_task_definitions + (task,),
        )

    def complete_resolution_gate(
        self,
        *,
        expected_revision: int,
        task_id: str,
    ) -> "ExecutionState":
        current = self.task_state(task_id)
        if current is None:
            raise KeyError(f"unknown task_id: {task_id}")
        if current.status not in {"PENDING", "READY"}:
            raise InvalidTaskTransition(
                task_id=task_id,
                source=current.status,
                target="COMPLETED",
            )
        completed = current.model_copy(
            update={"status": "COMPLETED", "reason_code": None}
        )
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name=f"resolution:{task_id}:COMPLETED",
            task_states=tuple(
                completed if task.task_id == task_id else task
                for task in self.task_states
            ),
        )

    def complete_resolution_with_gap(
        self,
        *,
        expected_revision: int,
        task_id: str,
        gap: dict[str, Any],
    ) -> "ExecutionState":
        current = self.task_state(task_id)
        if current is None:
            raise KeyError(f"unknown task_id: {task_id}")
        if current.status not in {"PENDING", "READY"}:
            raise InvalidTaskTransition(
                task_id=task_id,
                source=current.status,
                target="COMPLETED",
            )
        completed = current.model_copy(
            update={"status": "COMPLETED", "reason_code": None}
        )
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name=f"resolution:{task_id}:UNRESOLVED_GAP",
            task_states=tuple(
                completed if task.task_id == task_id else task
                for task in self.task_states
            ),
            unresolved_gaps=self.unresolved_gaps + (dict(gap),),
        )

    def complete_by_user(self, *, expected_revision: int) -> "ExecutionState":
        """Atomically terminate remaining work after an explicit user finish."""

        if expected_revision != self.revision:
            raise ExecutionStateConflict(
                expected_revision=expected_revision,
                actual_revision=self.revision,
            )
        terminal = {"COMPLETED", "SKIPPED", "CANCELED"}
        updated_tasks = tuple(
            task
            if task.status in terminal
            else task.model_copy(update={"status": "CANCELED", "reason_code": None})
            for task in self.task_states
        )
        return self._apply_transition(
            expected_revision=expected_revision,
            transition_name="execution:user-complete",
            task_states=updated_tasks,
            current_wait_handle=None,
            execution_status="COMPLETED",
        )

    def _apply_transition(
        self,
        *,
        expected_revision: int,
        transition_name: str,
        **changes: Any,
    ) -> "ExecutionState":
        if isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if not isinstance(transition_name, str) or not transition_name.strip():
            raise ValueError("transition_name must be non-empty")
        if expected_revision != self.revision:
            raise ExecutionStateConflict(
                expected_revision=expected_revision,
                actual_revision=self.revision,
            )
        mutable_fields = {
            "task_states",
            "task_attempts",
            "dynamic_task_definitions",
            "artifact_refs",
            "current_wait_handle",
            "latest_observation",
            "scheduler_step_count",
            "followups_total_used",
            "followups_by_question",
            "replans_used",
            "unresolved_gaps",
            "execution_status",
        }
        unknown = set(changes) - mutable_fields
        if unknown:
            raise ValueError(
                "transition cannot mutate immutable or unknown fields: "
                + ", ".join(sorted(unknown))
            )
        updated = self.model_dump(mode="python")
        updated.update(changes)
        updated["revision"] = self.revision + 1
        return type(self).model_validate(updated)

    def cas_update(
        self,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> "ExecutionState":
        """Short form for a domain transition guarded by revision/CAS."""

        return self.apply_transition(
            expected_revision=expected_revision,
            transition_name="cas_update",
            **changes,
        )


__all__ = [
    "ExecutionArtifactRef",
    "ExecutionState",
    "ExecutionStateConflict",
    "ExecutionStatus",
    "InvalidTaskTransition",
    "TaskRuntimeState",
    "TaskRuntimeStatus",
    "transition_task_state",
]
