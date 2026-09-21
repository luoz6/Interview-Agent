"""Deterministic Scheduler application capability.

This module owns orchestration policy at the application boundary.  It knows
how to load execution state, select a plan task, resolve a typed capability,
assemble a typed request, invoke an Agent through a neutral Port, and commit
the resulting observation back to :class:`ExecutionState`.  It deliberately
does not import A2A or any other transport implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, Protocol

from .policy import DeterministicSchedulerPolicy
from app.domain.agents.artifacts import DomainArtifact
from app.domain.execution_lease import LeaseBusy, LeaseLost
from app.domain.interview.scheduling.assembler import (
    RequestAssemblyError,
    assemble_agent_request,
)
from app.domain.interview.scheduling.capabilities import CapabilityDescriptor
from app.domain.interview.scheduling.compatibility import (
    validate_output_compatibility,
)
from app.domain.interview.scheduling.plan import (
    ExecutionPlan,
    ExecutionTaskDefinition,
)
from app.domain.interview.scheduling.commands import UserCommand
from app.domain.interview.scheduling.ledger import (
    InvocationIdentity,
    InvocationLedgerEntry,
)
from app.domain.interview.scheduling.conflicts import (
    UserCommandConflictOutcome,
    classify_user_command,
)
from app.domain.interview.scheduling.state import ExecutionState
from app.domain.interview.scheduling.waits import WaitHandle
from app.ports.agent_capability import AgentCapabilityPort
from app.ports.agent_invocation import AgentInvocationPort
from app.ports.agent_invocation_ledger import AgentInvocationLedgerPort
from app.ports.scheduler_commands import SchedulerCommandPort


class ExecutionStateStore(Protocol):
    """Minimal application boundary for the canonical runtime state."""

    def load(self, execution_id: str) -> ExecutionState: ...

    def save(self, state: ExecutionState) -> ExecutionState | None: ...

    def delete(self, execution_id: str) -> int: ...


class ExecutionPlanStore(Protocol):
    """Minimal lookup boundary for an immutable execution definition."""

    def load(self, execution_id: str) -> ExecutionPlan: ...


class UserCommandStore(Protocol):
    """Lookup/record boundary for command-idempotency facts."""

    def get(self, command_id: str) -> UserCommand | None: ...

    def put(self, command: UserCommand) -> None: ...


SchedulerAction = Literal[
    "DISPATCH",
    "WAIT_USER",
    "COMPLETE",
    "NOOP",
    "FAILED",
]


class SchedulerDispatchError(RuntimeError):
    """Raised when a valid plan task cannot be dispatched safely."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str | None = None,
        code: str = "dispatch_rejected",
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.code = code


class UserCommandDurableConflict(RuntimeError):
    """The durable command id was reused with a different payload."""

    def __init__(self, command_id: str) -> None:
        super().__init__(f"durable command payload conflict: {command_id}")
        self.command_id = command_id


@dataclass(frozen=True)
class SchedulerStepResult:
    """Result of one deterministic Scheduler application step."""

    action: SchedulerAction
    state: ExecutionState
    task: ExecutionTaskDefinition | None = None
    capability: CapabilityDescriptor | None = None
    request: Any | None = None
    artifact: DomainArtifact | None = None
    observation: Mapping[str, Any] | None = None
    error: Exception | None = None


@dataclass(frozen=True)
class UserCommandResult:
    """Outcome of applying one fenced command to a waiting execution."""

    outcome: UserCommandConflictOutcome
    state: ExecutionState

    @property
    def accepted(self) -> bool:
        return self.outcome.accepted


class InMemoryExecutionStateStore:
    """Small development/test state store; production may supply a durable one."""

    def __init__(self, *states: ExecutionState) -> None:
        self._states = {state.execution_id: state for state in states}
        self._deleted_execution_ids: set[str] = set()

    def load(self, execution_id: str) -> ExecutionState:
        try:
            return self._states[execution_id]
        except KeyError as exc:
            raise KeyError(f"execution state not found: {execution_id}") from exc

    def save(self, state: ExecutionState) -> ExecutionState:
        if state.execution_id in self._deleted_execution_ids:
            raise RuntimeError("deleted execution state cannot be recreated")
        self._states[state.execution_id] = state
        return state

    def delete(self, execution_id: str) -> int:
        existed = execution_id in self._states
        self._states.pop(execution_id, None)
        self._deleted_execution_ids.add(execution_id)
        return int(existed)


class InMemoryUserCommandStore:
    """Development/test command ledger used until the durable adapter task."""

    def __init__(self) -> None:
        self._commands: dict[str, UserCommand] = {}

    def get(self, command_id: str) -> UserCommand | None:
        return self._commands.get(command_id)

    def put(self, command: UserCommand) -> None:
        self._commands[command.command_id] = command


class SchedulerApplicationCapability:
    """Run one deterministic Scheduler step through neutral Ports.

    The class is an application use case, not an Agent identity.  It accepts
    either a fixed ``plan`` (useful for a request-scoped composition) or an
    ``plan_store``.  A ``step`` loads the canonical state, chooses the first
    dependency-ready task, dispatches it synchronously, and records the
    resulting artifact observation through immutable state transitions.
    """

    def __init__(
        self,
        *,
        state_store: ExecutionStateStore | None = None,
        execution_state_store: ExecutionStateStore | None = None,
        capability_port: AgentCapabilityPort,
        invocation_port: AgentInvocationPort,
        plan: ExecutionPlan | None = None,
        plan_store: ExecutionPlanStore | Callable[[str], ExecutionPlan] | None = None,
        policy: DeterministicSchedulerPolicy | None = None,
        command_store: UserCommandStore | None = None,
        durable_command_port: SchedulerCommandPort | None = None,
        command_enqueue_port: SchedulerCommandPort | None = None,
        command_port: SchedulerCommandPort | None = None,
        invocation_ledger: AgentInvocationLedgerPort | None = None,
        ledger_port: AgentInvocationLedgerPort | None = None,
        worker_id: str = "scheduler",
        lease_seconds: int = 300,
    ) -> None:
        resolved_store = state_store or execution_state_store
        if resolved_store is None:
            raise ValueError("state_store is required")
        if plan is None and plan_store is None:
            raise ValueError("plan or plan_store is required")
        self.state_store = resolved_store
        self.capability_port = capability_port
        self.invocation_port = invocation_port
        self.plan = plan
        self.plan_store = plan_store
        self.policy = policy or DeterministicSchedulerPolicy()
        self.command_store = command_store or InMemoryUserCommandStore()
        configured_ports = [
            port
            for port in (
                durable_command_port,
                command_enqueue_port,
                command_port,
            )
            if port is not None
        ]
        if len({id(port) for port in configured_ports}) > 1:
            raise ValueError(
                "durable command port aliases received different values; "
                "provide only one"
            )
        self.durable_command_port = (
            durable_command_port or command_enqueue_port or command_port
        )
        if (
            invocation_ledger is not None
            and ledger_port is not None
            and invocation_ledger is not ledger_port
        ):
            raise ValueError(
                "invocation_ledger and ledger_port are aliases; provide one"
            )
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        if isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.invocation_ledger = invocation_ledger or ledger_port
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def step(
        self,
        execution_id: str,
        *,
        plan: ExecutionPlan | None = None,
        execution_context: Any | None = None,
    ) -> SchedulerStepResult:
        """Load state and execute at most one valid next task."""

        state = self.state_store.load(execution_id)
        if state.execution_id != execution_id:
            raise SchedulerDispatchError(
                "execution state identity does not match requested execution",
                code="execution_identity_mismatch",
            )
        resolved_plan = plan or self._load_plan(execution_id)
        if resolved_plan.execution_id != execution_id:
            raise SchedulerDispatchError(
                "execution plan identity does not match requested execution",
                code="execution_identity_mismatch",
            )
        return self.run_once(
            plan=resolved_plan,
            state=state,
            execution_context=execution_context,
        )

    def run_once(
        self,
        *,
        plan: ExecutionPlan,
        state: ExecutionState,
        execution_context: Any | None = None,
    ) -> SchedulerStepResult:
        """Execute one step against an already-loaded state snapshot."""

        if plan.execution_id != state.execution_id:
            raise SchedulerDispatchError(
                "execution plan and state identities differ",
                code="execution_identity_mismatch",
            )
        decision = self.policy.decide(plan=plan, state=state)
        recovering_running = False
        selected_task_id = decision.task_id
        if (
            decision.action == "NOOP"
            and decision.reason_code == "awaiting_observation"
            and self.invocation_ledger is not None
        ):
            selected_task_id = next(
                (
                    task.task_id
                    for task in self._task_definitions(plan, state)
                    if (
                        state.task_state(task.task_id) is not None
                        and state.task_state(task.task_id).status == "RUNNING"
                    )
                ),
                None,
            )
            recovering_running = selected_task_id is not None
        if decision.action != "DISPATCH" and not recovering_running:
            if (
                decision.action == "WAIT_USER"
                and state.current_wait_handle is None
                and decision.reason_code == "user_answer_required"
            ):
                return self._enter_wait(
                    plan=plan,
                    state=state,
                    execution_context=execution_context,
                )
            if decision.action == "COMPLETE" and state.execution_status != "COMPLETED":
                state = state.apply_transition(
                    expected_revision=state.revision,
                    transition_name="execution:complete",
                    execution_status="COMPLETED",
                    scheduler_step_count=state.scheduler_step_count + 1,
                )
                self.state_store.save(state)
            return SchedulerStepResult(
                action=decision.action,
                state=state,
                task=next(
                    (
                        task
                        for task in self._task_definitions(plan, state)
                        if task.task_id == decision.task_id
                    ),
                    None,
                ),
                observation={"reason_code": decision.reason_code},
            )
        task = next(
            (
                task
                for task in self._task_definitions(plan, state)
                if task.task_id == selected_task_id
            ),
            None,
        )
        if task is None:
            raise SchedulerDispatchError(
                "policy selected an unknown task",
                code="policy_task_not_found",
            )

        capability = self.capability_port.resolve(
            agent_id=task.agent_id,
            skill=task.skill,
        )
        if capability is None:
            raise SchedulerDispatchError(
                f"capability is unavailable for {task.agent_id}:{task.skill}",
                task_id=task.task_id,
                code="capability_unavailable",
            )
        self._validate_task_contract(task, capability)
        if not self.capability_port.validate_compatibility(
            agent_id=task.agent_id,
            skill=task.skill,
            request_contract_id=capability.request_contract_id,
            request_contract_version=capability.request_contract_version,
            input_artifact_types=tuple(
                ref.artifact_type for ref in state.artifact_refs
            ),
            capability_version=capability.capability_version,
        ):
            raise SchedulerDispatchError(
                f"capability compatibility rejected task {task.task_id}",
                task_id=task.task_id,
                code="capability_incompatible",
            )

        ready_state = state if recovering_running else self._mark_ready_tasks(plan, state)
        request = self._assemble_request(task, ready_state)
        running_state = ready_state
        if not recovering_running:
            running_state = ready_state.transition_task(
                expected_revision=ready_state.revision,
                task_id=task.task_id,
                target_status="RUNNING",
            )
        if running_state.execution_status != "RUNNING":
            running_state = running_state.apply_transition(
                expected_revision=running_state.revision,
                transition_name="execution:running",
                execution_status="RUNNING",
            )
        ledger = self.invocation_ledger
        ledger_identity = None
        lease = None
        if ledger is not None:
            runtime_task = running_state.task_state(task.task_id)
            if runtime_task is None:
                raise SchedulerDispatchError(
                    f"running task state disappeared for {task.task_id}",
                    task_id=task.task_id,
                    code="task_state_missing",
                )
            ledger_identity = InvocationIdentity(
                execution_id=running_state.execution_id,
                task_id=task.task_id,
                logical_attempt=runtime_task.attempt,
            )
            request_digest = _request_digest(request)
            prepared = ledger.prepare(
                InvocationLedgerEntry(
                    identity=ledger_identity,
                    agent_id=task.agent_id,
                    skill=task.skill,
                    request_digest=request_digest,
                )
            )
            if prepared.request_digest != request_digest:
                raise SchedulerDispatchError(
                    f"invocation request changed for {task.task_id}",
                    task_id=task.task_id,
                    code="invocation_payload_conflict",
                )
            if prepared.status == "COMPLETED" and prepared.artifact_ref:
                return self._complete_from_ledger(
                    plan=plan,
                    task=task,
                    capability=capability,
                    request=request,
                    running_state=running_state,
                    artifact_ref=prepared.artifact_ref,
                )
            try:
                lease = ledger.acquire(
                    ledger_identity,
                    owner_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            except LeaseBusy as exc:
                if not recovering_running:
                    raise
                return SchedulerStepResult(
                    action="NOOP",
                    state=state,
                    task=task,
                    capability=capability,
                    request=request,
                    error=exc,
                )
            ledger.mark_running(
                ledger_identity,
                lease_owner=lease.owner_id,
                fencing_version=lease.fencing_version,
            )
        if not recovering_running:
            self.state_store.save(running_state)
        try:
            artifact = self.invocation_port.invoke(
                agent_id=task.agent_id,
                skill=task.skill,
                request=request,
                execution_context=execution_context,
            )
            if not isinstance(artifact, DomainArtifact):
                raise SchedulerDispatchError(
                    "Agent invocation returned a non-domain artifact",
                    task_id=task.task_id,
                    code="invalid_agent_output",
                )
            validate_output_compatibility(capability, artifact)
        except Exception as exc:
            if ledger is not None and ledger_identity is not None and lease is not None:
                stale_error = self._mark_ledger_failed(
                    ledger,
                    ledger_identity,
                    lease,
                    exc,
                )
                if stale_error is not None:
                    return self._stale_worker_result(
                        running_state=running_state,
                        task=task,
                        capability=capability,
                        request=request,
                        error=stale_error,
                    )
            failed_state = running_state.transition_task(
                expected_revision=running_state.revision,
                task_id=task.task_id,
                target_status="FAILED",
                reason_code=getattr(exc, "code", "dispatch_failed"),
            )
            failed_state = failed_state.apply_transition(
                expected_revision=failed_state.revision,
                transition_name=f"observation:{task.task_id}:failed",
                latest_observation={
                    "task_id": task.task_id,
                    "status": "FAILED",
                    "error_code": getattr(exc, "code", "dispatch_failed"),
                },
                execution_status="FAILED",
                scheduler_step_count=failed_state.scheduler_step_count + 1,
            )
            self.state_store.save(failed_state)
            return SchedulerStepResult(
                action="FAILED",
                state=failed_state,
                task=task,
                capability=capability,
                request=request,
                error=exc,
            )

        runtime_task = running_state.task_state(task.task_id)
        if runtime_task is None:
            raise SchedulerDispatchError(
                f"running task state disappeared for {task.task_id}",
                task_id=task.task_id,
                code="task_state_missing",
            )
        artifact_ref = _artifact_ref(
            execution_id=running_state.execution_id,
            task_id=task.task_id,
            attempt=runtime_task.attempt,
            artifact=artifact,
        )
        if ledger is not None and ledger_identity is not None and lease is not None:
            try:
                receipt = ledger.commit(
                    ledger_identity,
                    artifact_ref=artifact_ref,
                    lease_owner=lease.owner_id,
                    fencing_version=lease.fencing_version,
                )
                artifact_ref = receipt.artifact_ref
            except LeaseLost as exc:
                return self._stale_worker_result(
                    running_state=running_state,
                    task=task,
                    capability=capability,
                    request=request,
                    error=exc,
                )
            except Exception as exc:
                stale_error = self._mark_ledger_failed(
                    ledger,
                    ledger_identity,
                    lease,
                    exc,
                )
                if stale_error is not None:
                    return self._stale_worker_result(
                        running_state=running_state,
                        task=task,
                        capability=capability,
                        request=request,
                        error=stale_error,
                    )
                failed_state = running_state.transition_task(
                    expected_revision=running_state.revision,
                    task_id=task.task_id,
                    target_status="FAILED",
                    reason_code=getattr(exc, "code", "commit_failed"),
                )
                failed_state = failed_state.apply_transition(
                    expected_revision=failed_state.revision,
                    transition_name=f"observation:{task.task_id}:failed",
                    latest_observation={
                        "task_id": task.task_id,
                        "status": "FAILED",
                        "error_code": getattr(exc, "code", "commit_failed"),
                    },
                    execution_status="FAILED",
                    scheduler_step_count=failed_state.scheduler_step_count + 1,
                )
                self.state_store.save(failed_state)
                return SchedulerStepResult(
                    action="FAILED",
                    state=failed_state,
                    task=task,
                    capability=capability,
                    request=request,
                    error=exc,
                )
        # The task is marked COMPLETED only after the durable ledger has
        # accepted the logical artifact effect.  This is the MA2 commit
        # ordering; no completed state is persisted speculatively.
        completed_state = running_state.transition_task(
            expected_revision=running_state.revision,
            task_id=task.task_id,
            target_status="COMPLETED",
        )
        observation = {
            **_completed_observation_lineage(
                plan=plan,
                completed_state=completed_state,
                task=task,
                logical_attempt=runtime_task.attempt,
                artifact_ref=artifact_ref,
            ),
            "task_id": task.task_id,
            "status": "COMPLETED",
            "artifact_ref": artifact_ref,
            "artifact_type": artifact.artifact_type,
            "artifact_version": artifact.schema_version,
            "artifact": artifact.model_dump(mode="json"),
        }
        artifact_question_id = getattr(artifact, "question_id", None)
        if isinstance(artifact_question_id, str) and artifact_question_id.strip():
            observation["question_id"] = artifact_question_id
        observed_state = completed_state.apply_transition(
            expected_revision=completed_state.revision,
            transition_name=f"observation:{task.task_id}",
            artifact_refs=completed_state.artifact_refs
            + (
                {
                    "artifact_ref": artifact_ref,
                    "artifact_type": artifact.artifact_type,
                    "artifact_version": artifact.schema_version,
                    "task_id": task.task_id,
                },
            ),
            latest_observation=observation,
            execution_status=(
                "COMPLETED"
                if self._all_tasks_terminal(plan, completed_state)
                and artifact.artifact_type
                not in {"main-question-artifact", "followup-artifact"}
                else "RUNNING"
            ),
            scheduler_step_count=completed_state.scheduler_step_count + 1,
        )
        self.state_store.save(observed_state)
        return SchedulerStepResult(
            action="DISPATCH",
            state=observed_state,
            task=task,
            capability=capability,
            request=request,
            artifact=artifact,
            observation=observation,
        )

    def accept_user_command(
        self,
        execution_id: str,
        command: UserCommand,
        *,
        plan: ExecutionPlan | None = None,
    ) -> UserCommandResult:
        """Apply a fenced command; no unbound ``resume(answer)`` path exists."""

        state = self.state_store.load(execution_id)
        if state.execution_id != execution_id:
            raise SchedulerDispatchError(
                "execution state identity does not match requested execution",
                code="execution_identity_mismatch",
            )
        result = self.apply_user_command(state, command)
        if result.state is not state:
            self.state_store.save(result.state)
        return result

    def apply_user_command(
        self,
        state: ExecutionState,
        command: UserCommand,
    ) -> UserCommandResult:
        """Apply a fenced command to an explicit canonical state snapshot.

        Durable graph runtimes use this entry point so the checkpointed
        aggregate remains the state authority across process restarts.  The
        convenience ``accept_user_command`` method keeps the load/save wrapper
        for non-graph callers.
        """

        execution_id = state.execution_id
        if command.execution_id != execution_id:
            outcome = UserCommandConflictOutcome(
                command_id=command.command_id,
                disposition="REJECT",
                reason_code="wrong_execution",
            )
            return UserCommandResult(outcome=outcome, state=state)
        wait_handle = state.current_wait_handle
        if wait_handle is None:
            recorded = self.command_store.get(command.command_id)
            if recorded is None:
                durable_record = self._get_durable_command(command)
                if durable_record is not None:
                    disposition = (
                        "REPLAY"
                        if _durable_record_matches(command, durable_record)
                        else "CONFLICT"
                    )
                    reason_code = (
                        "idempotent_replay"
                        if disposition == "REPLAY"
                        else "command_payload_conflict"
                    )
                    outcome = UserCommandConflictOutcome(
                        command_id=command.command_id,
                        disposition=disposition,
                        reason_code=reason_code,
                    )
                    return UserCommandResult(outcome=outcome, state=state)
            if recorded is not None:
                disposition = (
                    "REPLAY"
                    if recorded.payload_sha256 == command.payload_sha256
                    else "CONFLICT"
                )
                reason_code = (
                    "idempotent_replay"
                    if disposition == "REPLAY"
                    else "command_payload_conflict"
                )
                outcome = UserCommandConflictOutcome(
                    command_id=command.command_id,
                    disposition=disposition,
                    reason_code=reason_code,
                )
            else:
                outcome = UserCommandConflictOutcome(
                    command_id=command.command_id,
                    disposition="REJECT",
                    reason_code="no_active_wait",
                )
            return UserCommandResult(outcome=outcome, state=state)
        outcome = classify_user_command(
            command,
            wait_handle=wait_handle,
            current_revision=state.revision,
            recorded_command=self.command_store.get(command.command_id),
        )
        if not outcome.accepted:
            return UserCommandResult(outcome=outcome, state=state)

        try:
            self._enqueue_durable_command(command)
        except UserCommandDurableConflict:
            conflict = UserCommandConflictOutcome(
                command_id=command.command_id,
                disposition="CONFLICT",
                reason_code="command_payload_conflict",
            )
            return UserCommandResult(outcome=conflict, state=state)
        next_state = state.apply_transition(
            expected_revision=state.revision,
            transition_name=f"command:{command.command_id}",
            current_wait_handle=None,
            latest_observation={
                "status": "ANSWER_RECEIVED",
                "task_id": command.task_id,
                "question_id": command.question_id,
                "command_id": command.command_id,
                "payload": dict(command.payload),
            },
            execution_status="RUNNING",
            scheduler_step_count=state.scheduler_step_count + 1,
        )
        self.command_store.put(command)
        return UserCommandResult(outcome=outcome, state=next_state)

    def _get_durable_command(self, command: UserCommand) -> Any | None:
        port = self.durable_command_port
        getter = getattr(port, "get", None) if port is not None else None
        if getter is None:
            return None
        return getter(
            execution_id=command.execution_id,
            command_id=command.command_id,
        )

    def _enqueue_durable_command(self, command: UserCommand) -> None:
        """Fence a command in durable storage before mutating ExecutionState.

        The in-memory command ledger remains useful for request-local replay
        checks, but when a durable adapter is configured it is never the
        source of truth for enqueueing.  A payload conflict is translated to
        the same exception-neutral signal used by the existing runtime; all
        other adapter failures are allowed to surface without applying the
        state transition.
        """

        port = self.durable_command_port
        if port is None:
            return
        command_type = _durable_command_type(command.command_kind)
        payload = dict(command.payload)
        try:
            port.enqueue(
                execution_id=command.execution_id,
                command_id=command.command_id,
                command_type=command_type,
                expected_version=command.expected_revision,
                payload=payload,
            )
        except Exception as exc:
            # Do not hide infrastructure failures.  The caller must not
            # advance state after an enqueue error.  The canonical Postgres
            # adapter raises CommandPayloadConflict, and keeping this check by
            # semantic class/code avoids coupling the application to it.
            if (
                exc.__class__.__name__ == "CommandPayloadConflict"
                or getattr(exc, "code", None) == "command_payload_conflict"
            ):
                raise UserCommandDurableConflict(command.command_id) from exc
            raise

    def _enter_wait(
        self,
        *,
        plan: ExecutionPlan,
        state: ExecutionState,
        execution_context: Any | None = None,
    ) -> SchedulerStepResult:
        observation = state.latest_observation or {}
        task_id = observation.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise SchedulerDispatchError(
                "cannot create WAIT_USER without a producing task",
                code="wait_task_missing",
            )
        question_id = observation.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            task = next(
                (
                    item
                    for item in self._task_definitions(plan, state)
                    if item.task_id == task_id
                ),
                None,
            )
            question_id = (
                task.parameters.get("question_id") if task is not None else None
            )
        if not isinstance(question_id, str) or not question_id.strip():
            raise SchedulerDispatchError(
                f"cannot create WAIT_USER without question id for {task_id}",
                task_id=task_id,
                code="wait_question_missing",
            )
        issued_revision = state.revision + 1
        wait_handle = WaitHandle(
            wait_id=f"wait:{state.execution_id}:{task_id}:{issued_revision}",
            execution_id=state.execution_id,
            task_id=task_id,
            question_id=question_id,
            issued_revision=issued_revision,
            expected_command_kind="ANSWER",
        )
        waiting_state = state.apply_transition(
            expected_revision=state.revision,
            transition_name=f"wait:{task_id}",
            current_wait_handle=wait_handle,
            execution_status="WAITING",
            scheduler_step_count=state.scheduler_step_count + 1,
        )
        self.state_store.save(waiting_state)
        return SchedulerStepResult(
            action="WAIT_USER",
            state=waiting_state,
            observation={
                **dict(observation),
                "status": "WAITING",
                "wait_id": wait_handle.wait_id,
                "question_id": wait_handle.question_id,
            },
        )

    def _complete_from_ledger(
        self,
        *,
        plan: ExecutionPlan,
        task: ExecutionTaskDefinition,
        capability: CapabilityDescriptor,
        request: Any,
        running_state: ExecutionState,
        artifact_ref: str,
    ) -> SchedulerStepResult:
        """Project a durable completion after a scheduler-process restart."""

        completed_state = running_state.transition_task(
            expected_revision=running_state.revision,
            task_id=task.task_id,
            target_status="COMPLETED",
        )
        runtime_task = completed_state.task_state(task.task_id)
        if runtime_task is None:
            raise SchedulerDispatchError(
                f"completed task state disappeared for {task.task_id}",
                task_id=task.task_id,
                code="task_state_missing",
            )
        observation = {
            **_completed_observation_lineage(
                plan=plan,
                completed_state=completed_state,
                task=task,
                logical_attempt=runtime_task.attempt,
                artifact_ref=artifact_ref,
            ),
            "task_id": task.task_id,
            "status": "COMPLETED",
            "artifact_ref": artifact_ref,
            "artifact_type": capability.output_artifact_type,
            "artifact_version": capability.output_artifact_version,
            "durable_replay": True,
        }
        question_id = task.parameters.get("question_id")
        if isinstance(question_id, str) and question_id.strip():
            observation["question_id"] = question_id
        observed_state = completed_state.apply_transition(
            expected_revision=completed_state.revision,
            transition_name=f"observation:{task.task_id}:durable-replay",
            artifact_refs=completed_state.artifact_refs
            + (
                {
                    "artifact_ref": artifact_ref,
                    "artifact_type": capability.output_artifact_type,
                    "artifact_version": capability.output_artifact_version,
                    "task_id": task.task_id,
                },
            ),
            latest_observation=observation,
            execution_status=(
                "COMPLETED"
                if self._all_tasks_terminal(plan, completed_state)
                and capability.output_artifact_type
                not in {"main-question-artifact", "followup-artifact"}
                else "RUNNING"
            ),
            scheduler_step_count=completed_state.scheduler_step_count + 1,
        )
        self.state_store.save(observed_state)
        return SchedulerStepResult(
            action="DISPATCH",
            state=observed_state,
            task=task,
            capability=capability,
            request=request,
            observation=observation,
        )

    @staticmethod
    def _mark_ledger_failed(
        ledger: AgentInvocationLedgerPort,
        identity: InvocationIdentity,
        lease: Any,
        error: Exception,
    ) -> LeaseLost | None:
        try:
            ledger.mark_failed(
                identity,
                error_result={
                    "code": getattr(error, "code", "dispatch_failed"),
                    "message": str(error),
                },
                lease_owner=lease.owner_id,
                fencing_version=lease.fencing_version,
            )
        except LeaseLost as exc:
            return exc
        except Exception:
            # The original invocation/commit error remains the actionable
            # result; recovery can reclaim the durable ledger lease later.
            return None
        return None

    def _stale_worker_result(
        self,
        *,
        running_state: ExecutionState,
        task: ExecutionTaskDefinition,
        capability: CapabilityDescriptor,
        request: Any,
        error: LeaseLost,
    ) -> SchedulerStepResult:
        """Return the durable winner without allowing a stale state write."""

        return SchedulerStepResult(
            action="NOOP",
            state=self.state_store.load(running_state.execution_id),
            task=task,
            capability=capability,
            request=request,
            error=error,
        )

    def _load_plan(self, execution_id: str) -> ExecutionPlan:
        if self.plan is not None:
            return self.plan
        loader = self.plan_store
        if callable(loader):
            return loader(execution_id)
        if loader is None:
            raise SchedulerDispatchError("execution plan is not configured")
        return loader.load(execution_id)

    @staticmethod
    def _find_next_task(
        plan: ExecutionPlan,
        state: ExecutionState,
    ) -> ExecutionTaskDefinition | None:
        task_definitions = SchedulerApplicationCapability._task_definitions(
            plan, state
        )
        completed = {
            item.task_id
            for item in state.task_states
            if item.status in {"COMPLETED", "SKIPPED"}
        }
        runtime_by_id = {item.task_id: item for item in state.task_states}
        dependencies = {
            task.task_id: {
                dependency.predecessor_task_id
                for dependency in plan.dependency_definitions
                if dependency.successor_task_id == task.task_id
            }
            for task in task_definitions
        }
        for task in task_definitions:
            if task in state.dynamic_task_definitions:
                dependencies[task.task_id].update(
                    dependency
                    for dependency in task.parameters.get("dependencies", ())
                    if isinstance(dependency, str) and dependency
                )
            runtime = runtime_by_id.get(task.task_id)
            if runtime is None or runtime.status not in {"PENDING", "READY"}:
                continue
            if dependencies[task.task_id] <= completed:
                return task
        return None

    @staticmethod
    def _mark_ready_tasks(
        plan: ExecutionPlan,
        state: ExecutionState,
    ) -> ExecutionState:
        current = state
        while True:
            task = SchedulerApplicationCapability._find_next_task(plan, current)
            if task is None:
                return current
            runtime = current.task_state(task.task_id)
            if runtime is None or runtime.status != "PENDING":
                return current
            current = current.transition_task(
                expected_revision=current.revision,
                task_id=task.task_id,
                target_status="READY",
            )

    @staticmethod
    def _assemble_request(
        task: ExecutionTaskDefinition,
        state: ExecutionState,
    ) -> Any:
        try:
            return assemble_agent_request(task, state)
        except RequestAssemblyError:
            raise

    @staticmethod
    def _validate_task_contract(
        task: ExecutionTaskDefinition,
        capability: CapabilityDescriptor,
    ) -> None:
        if (
            task.input_contract is not None
            and task.input_contract != capability.request_contract_id
        ):
            raise SchedulerDispatchError(
                f"task {task.task_id} input contract does not match capability",
                task_id=task.task_id,
                code="request_contract_mismatch",
            )
        if (
            task.output_contract is not None
            and task.output_contract != capability.output_artifact_type
        ):
            raise SchedulerDispatchError(
                f"task {task.task_id} output contract does not match capability",
                task_id=task.task_id,
                code="output_contract_mismatch",
            )

    @staticmethod
    def _all_tasks_terminal(
        plan: ExecutionPlan,
        state: ExecutionState,
    ) -> bool:
        runtime_by_id = {item.task_id: item for item in state.task_states}
        return all(
            runtime_by_id.get(task.task_id) is not None
            and runtime_by_id[task.task_id].status
            in {"COMPLETED", "SKIPPED", "CANCELED"}
            for task in SchedulerApplicationCapability._task_definitions(plan, state)
        )

    @staticmethod
    def _task_definitions(
        plan: ExecutionPlan,
        state: ExecutionState,
    ) -> tuple[ExecutionTaskDefinition, ...]:
        """Return immutable plan tasks plus canonical dynamic definitions."""

        return plan.task_definitions + state.dynamic_task_definitions


def _artifact_ref(
    *,
    execution_id: str,
    task_id: str,
    attempt: int,
    artifact: DomainArtifact,
) -> str:
    payload = json.dumps(
        artifact.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{execution_id}/{task_id}/attempt-{attempt}/{digest}"


def _completed_observation_lineage(
    *,
    plan: ExecutionPlan,
    completed_state: ExecutionState,
    task: ExecutionTaskDefinition,
    logical_attempt: int,
    artifact_ref: str,
) -> dict[str, Any]:
    """Return stable links from the source plan through the committed effect."""

    identity = InvocationIdentity(
        execution_id=completed_state.execution_id,
        task_id=task.task_id,
        logical_attempt=logical_attempt,
    )
    return {
        "observation_ref": f"{artifact_ref}/observation",
        "interview_plan_ref": plan.interview_plan_ref,
        "execution_plan_revision": plan.definition_revision,
        "execution_id": completed_state.execution_id,
        "execution_state_revision": completed_state.revision + 1,
        "logical_invocation": identity.model_dump(mode="json"),
    }


def _request_digest(request: Any) -> str:
    """Hash the typed request payload for durable invocation identity facts."""

    if hasattr(request, "model_dump"):
        payload = request.model_dump(mode="json")
    else:
        payload = request
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


# Service is a compatibility spelling for application composition; both names
# refer to the same use case and no SchedulerAgent facade is introduced.
SchedulerApplicationService = SchedulerApplicationCapability


__all__ = [
    "ExecutionPlanStore",
    "ExecutionStateStore",
    "InMemoryExecutionStateStore",
    "InMemoryUserCommandStore",
    "SchedulerApplicationCapability",
    "SchedulerApplicationService",
    "SchedulerDispatchError",
    "SchedulerStepResult",
    "UserCommandDurableConflict",
    "UserCommandResult",
    "UserCommandStore",
]


def _durable_command_type(command_kind: str) -> str:
    """Map neutral command kinds onto the legacy durable command vocabulary."""

    mapping = {
        "ANSWER": "answer",
        "SKIP": "skip",
        "COMPLETE": "finish",
    }
    try:
        return mapping[command_kind]
    except KeyError as exc:
        raise SchedulerDispatchError(
            f"command kind cannot be durably enqueued: {command_kind}",
            code="unsupported_command_kind",
        ) from exc


def _durable_record_matches(command: UserCommand, record: Any) -> bool:
    """Compare the durable store's legacy projection with a UserCommand."""

    def value(name: str, default: Any = None) -> Any:
        if isinstance(record, Mapping):
            return record.get(name, default)
        return getattr(record, name, default)

    if value("command_id") != command.command_id:
        return False
    try:
        expected_type = _durable_command_type(command.command_kind)
    except SchedulerDispatchError:
        return False
    if value("command_type") != expected_type:
        return False
    if value("expected_version") != command.expected_revision:
        return False
    expected_answer = command.payload.get("answer_text")
    return value("answer_text") == expected_answer
