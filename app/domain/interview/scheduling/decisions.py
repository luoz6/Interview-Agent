"""Structured adaptive Scheduler decisions and deterministic validation."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.interview.scheduling.assembler import assemble_agent_request
from app.domain.interview.scheduling.context import SchedulerContext
from app.domain.interview.scheduling.plan import ExecutionTaskDefinition
from app.domain.interview.scheduling.requests import AgentRequest
from app.domain.interview.scheduling.state import ExecutionState, TaskRuntimeState


SchedulingDecisionAction = Literal[
    "DISPATCH",
    "WAIT_USER",
    "RETRY",
    "ADD_TASK",
    "SKIP_TASK",
    "CANCEL_TASK",
    "COMPLETE",
]

_REQUIRED_TASK_ACTIONS = frozenset(
    {"DISPATCH", "RETRY", "SKIP_TASK", "CANCEL_TASK"}
)
_FORBIDDEN_TASK_ACTIONS = frozenset({"ADD_TASK", "COMPLETE"})
_TERMINAL_TASK_STATUSES = frozenset({"COMPLETED", "SKIPPED", "CANCELED"})
_INSUFFICIENT_EVIDENCE_STATUSES = frozenset(
    {"INSUFFICIENT_EVIDENCE", "insufficient_evidence", "EVIDENCE_INSUFFICIENT"}
)
_EVIDENCE_ACQUISITION_SKILLS = frozenset(
    {"generate-main-question", "generate-followup"}
)


class DynamicTaskDeclaration(BaseModel):
    """Closed ADD_TASK proposal; request construction stays assembler-owned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str = Field(min_length=1, max_length=200)
    required_inputs: tuple[str, ...] = Field(default=(), max_length=16)
    artifact_refs: tuple[str, ...] = Field(default=(), max_length=32)
    reason: str = Field(min_length=1, max_length=1_000)
    dependencies: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_declaration_lists(self) -> "DynamicTaskDeclaration":
        for field_name in ("required_inputs", "artifact_refs", "dependencies"):
            values = getattr(self, field_name)
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field_name} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class SchedulingDecision(BaseModel):
    """Provider-neutral decision proposal with no arbitrary payload channel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: SchedulingDecisionAction
    task_id: str | None = Field(default=None, min_length=1)
    add_task: DynamicTaskDeclaration | None = None
    reason_code: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )

    @model_validator(mode="after")
    def validate_action_shape(self) -> "SchedulingDecision":
        if self.action in _REQUIRED_TASK_ACTIONS and self.task_id is None:
            raise ValueError(f"{self.action} requires task_id")
        if self.action in _FORBIDDEN_TASK_ACTIONS and self.task_id is not None:
            raise ValueError(f"{self.action} does not accept task_id")
        if self.action == "ADD_TASK" and self.add_task is None:
            raise ValueError("ADD_TASK requires add_task declaration")
        if self.action != "ADD_TASK" and self.add_task is not None:
            raise ValueError(f"{self.action} does not accept add_task declaration")
        return self


class SchedulingDecisionValidationError(ValueError):
    """A structured proposal is invalid for the canonical Scheduler context."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BoundedLoopExceeded(SchedulingDecisionValidationError):
    """A Scheduler hard limit would be exceeded by the proposed step."""

    def __init__(self, code: str, limit: str, message: str) -> None:
        super().__init__(code, message)
        self.limit = limit


def validate_bounded_loop(
    context: SchedulerContext,
    decision: SchedulingDecision | None = None,
) -> None:
    """Fail closed when any configured Scheduler loop limit is exhausted."""

    budget = context.budget
    if budget.remaining_scheduler_steps == 0 or (
        budget.max_scheduler_steps is not None
        and context.execution_state.scheduler_step_count >= budget.max_scheduler_steps
    ):
        _bounded_invalid(
            "scheduler_step_budget_exhausted",
            "max_scheduler_steps",
            "maximum Scheduler steps exhausted",
        )
    if (
        budget.execution_timeout_seconds is not None
        and budget.elapsed_execution_seconds >= budget.execution_timeout_seconds
    ):
        _bounded_invalid(
            "execution_timeout_exhausted",
            "execution_timeout",
            "execution timeout exhausted",
        )
    if decision is None:
        return

    action = decision.action
    if action in {"DISPATCH", "RETRY", "ADD_TASK"}:
        _check_remaining_limit(
            budget.max_agent_calls,
            budget.remaining_agent_calls,
            budget.agent_calls_used,
            "agent_calls_exhausted",
            "max_agent_calls",
        )
    if action == "RETRY":
        _check_remaining_limit(
            budget.max_retries,
            budget.remaining_retries,
            budget.retries_used,
            "retry_budget_exhausted",
            "max_retries",
        )
    if action == "ADD_TASK" and decision.add_task is not None:
        if decision.reason_code in {"evidence_insufficient", "replan"}:
            _check_remaining_limit(
                budget.max_replans,
                budget.remaining_replans,
                budget.replans_used,
                "replan_budget_exhausted",
                "max_replans",
            )
        if decision.add_task.capability in {"generate-followup", "interview-examiner:generate-followup"}:
            _check_remaining_limit(
                budget.max_followups,
                budget.remaining_followups,
                budget.followups_used,
                "followup_budget_exhausted",
                "max_followups",
            )
        if decision.add_task.capability in {"generate-main-question", "interview-examiner:generate-main-question"}:
            _check_remaining_limit(
                budget.max_questions,
                budget.remaining_questions,
                budget.questions_used,
                "question_budget_exhausted",
                "max_questions",
            )


def _check_remaining_limit(
    maximum: int | None,
    remaining: int | None,
    used: int,
    code: str,
    limit: str,
) -> None:
    if (remaining is not None and remaining <= 0) or (
        maximum is not None and used >= maximum
    ):
        _bounded_invalid(code, limit, f"{limit} exhausted")


def _bounded_invalid(code: str, limit: str, message: str) -> None:
    raise BoundedLoopExceeded(code, limit, message)


def validate_scheduling_decision(
    context: SchedulerContext,
    decision: SchedulingDecision,
) -> SchedulingDecision:
    """Fail closed unless a decision is valid for current durable state."""

    validate_bounded_loop(context, decision)
    action = decision.action

    if action == "DISPATCH":
        _validate_dispatch(context, decision)
    elif action == "WAIT_USER":
        _validate_wait_user(context, decision)
    elif action == "RETRY":
        runtime = _require_task(context, decision)
        if runtime.status != "FAILED":
            _invalid("retry_status_invalid", "RETRY requires a FAILED task")
        if runtime.attempt >= runtime.max_attempts:
            _invalid("retry_attempts_exhausted", "RETRY task has no attempts remaining")
    elif action == "ADD_TASK":
        if context.budget.remaining_task_slots == 0:
            _invalid("task_budget_exhausted", "ADD_TASK requires a remaining task slot")
        _validate_dynamic_task_declaration(context, decision.add_task)
    elif action == "SKIP_TASK":
        runtime = _require_task(context, decision)
        if runtime.status not in {"PENDING", "READY", "FAILED", "WAITING"}:
            _invalid(
                "skip_status_invalid",
                "SKIP_TASK requires a pending, ready, failed, or waiting task",
            )
    elif action == "CANCEL_TASK":
        runtime = _require_task(context, decision)
        if runtime.status in _TERMINAL_TASK_STATUSES:
            _invalid("cancel_status_invalid", "CANCEL_TASK requires a non-terminal task")
    elif action == "COMPLETE":
        state = context.execution_state
        if state.current_wait_handle is not None:
            _invalid("active_wait", "COMPLETE is invalid while a wait handle is active")
        if any(
            task.status not in _TERMINAL_TASK_STATUSES for task in state.task_states
        ):
            _invalid("tasks_not_terminal", "COMPLETE requires every task to be terminal")
    return decision


def _resolve_declared_capability(context: SchedulerContext, declaration: DynamicTaskDeclaration):
    matches = tuple(
        capability
        for capability in context.capabilities
        if declaration.capability
        in {capability.capability_key, f"{capability.agent_id}:{capability.skill}", capability.skill}
    )
    if len(matches) != 1:
        _invalid("capability_unavailable", "ADD_TASK capability must resolve to exactly one advertised capability")
    return matches[0]


def _validate_dynamic_task_declaration(
    context: SchedulerContext,
    declaration: DynamicTaskDeclaration | None,
) -> None:
    if declaration is None:
        _invalid("missing_task_declaration", "ADD_TASK requires a task declaration")
    capability = _resolve_declared_capability(context, declaration)
    required_inputs = set(declaration.required_inputs)
    if set(capability.required_input_artifact_types) - required_inputs:
        _invalid("required_input_missing", "ADD_TASK declaration omits capability-required inputs")
    summary_by_ref = {summary.artifact_ref: summary for summary in context.artifact_summaries}
    state_refs = {ref.artifact_ref for ref in context.execution_state.artifact_refs}
    if set(declaration.artifact_refs) - set(summary_by_ref) - state_refs:
        _invalid("artifact_ref_missing", "ADD_TASK references an unavailable artifact")
    available_types = {
        summary.artifact_type
        for summary in context.artifact_summaries
        if summary.artifact_ref in declaration.artifact_refs
    }
    available_types.update(
        ref.artifact_type
        for ref in context.execution_state.artifact_refs
        if ref.artifact_ref in declaration.artifact_refs
    )
    if not set(capability.required_input_artifact_types).issubset(available_types):
        _invalid("required_artifact_missing", "ADD_TASK lacks artifacts required by the capability")
    known_task_ids = {task.task_id for task in context.execution_state.task_states} | {
        task.task_id for task in context.execution_state.dynamic_task_definitions
    }
    if set(declaration.dependencies) - known_task_ids:
        _invalid("dependency_unknown", "ADD_TASK dependency is not in the execution")
    task = materialize_dynamic_task(context, declaration)
    if task.task_id in known_task_ids:
        _invalid("task_duplicate", "ADD_TASK declaration resolves to an existing task")


def materialize_dynamic_task(
    context: SchedulerContext,
    declaration: DynamicTaskDeclaration,
) -> ExecutionTaskDefinition:
    """Create a deterministic task definition from a validated declaration."""

    capability = _resolve_declared_capability(context, declaration)
    payload = {
        "execution_id": context.execution_state.execution_id,
        "capability": declaration.capability,
        "required_inputs": declaration.required_inputs,
        "artifact_refs": declaration.artifact_refs,
        "reason": declaration.reason,
        "dependencies": declaration.dependencies,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    parameters = {
        "required_artifact_types": declaration.required_inputs,
        "artifact_refs": declaration.artifact_refs,
        "reason": declaration.reason,
        "dependencies": declaration.dependencies,
    }
    latest_observation = context.execution_state.latest_observation or {}
    for field_name in ("question_id", "focus", "intent"):
        value = latest_observation.get(field_name)
        if value is not None:
            parameters[field_name] = value
    return ExecutionTaskDefinition(
        task_id=f"dynamic-{digest}",
        capability=declaration.capability,
        agent_id=capability.agent_id,
        skill=capability.skill,
        input_contract=capability.request_contract_id,
        output_contract=capability.output_artifact_type,
        parameters=parameters,
    )


def register_dynamic_task(state: ExecutionState, task: ExecutionTaskDefinition) -> ExecutionState:
    """Register one materialized task through the canonical state transition."""

    try:
        return state.add_dynamic_task(expected_revision=state.revision, task=task)
    except ValueError as exc:
        _invalid("task_duplicate", str(exc))


def assemble_dynamic_task_request(
    context: SchedulerContext,
    declaration: DynamicTaskDeclaration,
) -> tuple[ExecutionTaskDefinition, AgentRequest]:
    """Materialize a declaration and build its typed request via MA1 assembler."""

    _validate_dynamic_task_declaration(context, declaration)
    task = materialize_dynamic_task(context, declaration)
    selected_refs = tuple(
        ref
        for ref in context.execution_state.artifact_refs
        if ref.artifact_ref in declaration.artifact_refs
    )
    return task, assemble_agent_request(task, context.execution_state, artifact_refs=selected_refs)


def is_evidence_insufficient(context: SchedulerContext) -> bool:
    """Return whether the latest bounded Reviewer observation requests replanning."""

    observations = context.recent_observations
    if observations:
        status = observations[-1].status
        return status in _INSUFFICIENT_EVIDENCE_STATUSES
    latest = context.execution_state.latest_observation or {}
    return latest.get("status") in _INSUFFICIENT_EVIDENCE_STATUSES


def validate_evidence_insufficient_replan(
    context: SchedulerContext,
    decision: SchedulingDecision,
) -> SchedulingDecision:
    """Require insufficient evidence to replan into an acquisition task."""

    if not is_evidence_insufficient(context):
        _invalid(
            "replan_not_triggered",
            "evidence-insufficient replan requires an insufficient Reviewer observation",
        )
    if decision.action != "ADD_TASK" or decision.add_task is None:
        _invalid(
            "replan_action_invalid",
            "insufficient evidence must produce an ADD_TASK acquisition decision",
        )
    capability = _resolve_declared_capability(context, decision.add_task)
    if capability.skill not in _EVIDENCE_ACQUISITION_SKILLS:
        _invalid(
            "replan_capability_invalid",
            "insufficient evidence must acquire a question or follow-up",
        )
    latest_task_id = (context.execution_state.latest_observation or {}).get("task_id")
    if isinstance(latest_task_id, str) and latest_task_id:
        if latest_task_id not in decision.add_task.dependencies:
            _invalid(
                "replan_dependency_missing",
                "acquisition task must depend on the insufficient Reviewer task",
            )
    return validate_scheduling_decision(context, decision)


def build_evidence_insufficient_replan(
    context: SchedulerContext,
    declaration: DynamicTaskDeclaration,
) -> SchedulingDecision:
    """Build and validate the only adaptive step allowed by MA5-T05."""

    decision = SchedulingDecision(
        action="ADD_TASK",
        reason_code="evidence_insufficient",
        add_task=declaration,
    )
    return validate_evidence_insufficient_replan(context, decision)


def execute_evidence_insufficient_replan(
    context: SchedulerContext,
    declaration: DynamicTaskDeclaration,
) -> tuple[ExecutionState, ExecutionTaskDefinition, AgentRequest]:
    """Register the acquisition task and assemble the next typed Agent request."""

    build_evidence_insufficient_replan(context, declaration)
    task, request = assemble_dynamic_task_request(context, declaration)
    latest = context.execution_state.latest_observation or {}
    question_id = latest.get("question_id")
    if not isinstance(question_id, str) or not question_id:
        _invalid("question_id_missing", "follow-up pair requires question_id")
    ordinal = context.execution_state.followups_by_question.get(question_id, 0) + 1
    followup_id = f"followup:{question_id}:{ordinal}"
    followup = task.model_copy(
        update={
            "task_id": followup_id,
            "parameters": {
                **task.parameters,
                "question_id": question_id,
                "replan_ordinal": ordinal,
            },
        }
    )
    reviewer = next(
        (
            capability
            for capability in context.capabilities
            if capability.skill == "evaluate-answer"
        ),
        None,
    )
    if reviewer is None:
        _invalid("review_capability_unavailable", "follow-up pair requires Reviewer")
    evaluation = ExecutionTaskDefinition(
        task_id=f"evaluate-followup:{question_id}:{ordinal}",
        capability="interview.answer-evaluation",
        agent_id=reviewer.agent_id,
        skill=reviewer.skill,
        input_contract=reviewer.request_contract_id,
        output_contract=reviewer.output_artifact_type,
        parameters={
            "phase": "followup_evaluation",
            "question_id": question_id,
            "dependencies": (followup_id,),
            "parent_review_task_id": latest.get("task_id"),
            "replan_ordinal": ordinal,
        },
    )
    state = context.execution_state.register_followup_pair(
        expected_revision=context.execution_state.revision,
        question_id=question_id,
        followup=followup,
        evaluation=evaluation,
    )
    return state, followup, request


def derive_adaptive_followup_decision(
    context: SchedulerContext,
) -> SchedulingDecision:
    """Derive a legal next step from the latest candidate observation.

    The InterviewPlan slice is held constant by the caller.  Candidate
    observations change only the bounded branch: insufficient evidence acquires
    a follow-up question, while sufficient evidence dispatches the ready
    Reviewer task.  No provider output is trusted without deterministic
    validation.
    """

    if not context.recent_observations:
        _invalid("observation_missing", "adaptive follow-up requires an observation")
    latest = context.recent_observations[-1]
    if is_evidence_insufficient(context):
        followup_capabilities = tuple(
            capability
            for capability in context.capabilities
            if capability.skill == "generate-followup"
        )
        if len(followup_capabilities) != 1:
            _invalid(
                "followup_capability_unavailable",
                "adaptive follow-up requires exactly one follow-up capability",
            )
        declaration = DynamicTaskDeclaration(
            capability=followup_capabilities[0].skill,
            reason="candidate evidence is insufficient",
            dependencies=(latest.task_id,),
        )
        return build_evidence_insufficient_replan(context, declaration)

    if latest.status in {"SUFFICIENT_EVIDENCE", "sufficient_evidence", "EVIDENCE_SUFFICIENT"}:
        if not context.ready_tasks:
            _invalid(
                "review_task_missing",
                "sufficient evidence requires a ready Reviewer task",
            )
        task = sorted(context.ready_tasks, key=lambda item: item.task_id)[0]
        decision = SchedulingDecision(
            action="DISPATCH",
            task_id=task.task_id,
            reason_code="evidence_sufficient_review",
        )
        return validate_scheduling_decision(context, decision)

    decision = SchedulingDecision(action="WAIT_USER", reason_code="awaiting_observation")
    return validate_scheduling_decision(context, decision)


def _validate_dispatch(
    context: SchedulerContext,
    decision: SchedulingDecision,
) -> None:
    task = next(
        (item for item in context.ready_tasks if item.task_id == decision.task_id),
        None,
    )
    if task is None:
        _invalid("task_not_ready", "DISPATCH requires a task in ready_tasks")
    compatible = any(
        capability.agent_id == task.agent_id
        and capability.skill == task.skill
        and (
            task.input_contract is None
            or capability.request_contract_id == task.input_contract
        )
        and (
            task.output_contract is None
            or capability.output_artifact_type == task.output_contract
        )
        for capability in context.capabilities
    )
    if not compatible:
        _invalid(
            "capability_unavailable",
            "DISPATCH requires a compatible advertised capability",
        )


def _validate_wait_user(
    context: SchedulerContext,
    decision: SchedulingDecision,
) -> None:
    state = context.execution_state
    eligible_task_ids = {
        task.task_id for task in state.task_states if task.status == "WAITING"
    }
    if state.current_wait_handle is not None:
        eligible_task_ids.add(state.current_wait_handle.task_id)
    observation = state.latest_observation or {}
    if (
        observation.get("status") == "COMPLETED"
        and observation.get("artifact_type")
        in {"main-question-artifact", "followup-artifact"}
        and isinstance(observation.get("task_id"), str)
    ):
        eligible_task_ids.add(observation["task_id"])
    if not eligible_task_ids:
        _invalid("wait_not_required", "WAIT_USER requires a durable user-wait signal")
    if decision.task_id is not None and decision.task_id not in eligible_task_ids:
        _invalid("wait_task_mismatch", "WAIT_USER task_id does not match the wait signal")


def _require_task(context: SchedulerContext, decision: SchedulingDecision):
    runtime = context.execution_state.task_state(decision.task_id or "")
    if runtime is None:
        _invalid("task_unknown", f"unknown task_id: {decision.task_id}")
    return runtime


def _require_step_budget(context: SchedulerContext) -> None:
    if context.budget.remaining_scheduler_steps == 0:
        _invalid("scheduler_step_budget_exhausted", "no scheduler steps remain")


def _invalid(code: str, message: str) -> None:
    raise SchedulingDecisionValidationError(code, message)


__all__ = [
    "DynamicTaskDeclaration",
    "BoundedLoopExceeded",
    "SchedulingDecision",
    "SchedulingDecisionAction",
    "SchedulingDecisionValidationError",
    "assemble_dynamic_task_request",
    "build_evidence_insufficient_replan",
    "execute_evidence_insufficient_replan",
    "derive_adaptive_followup_decision",
    "is_evidence_insufficient",
    "materialize_dynamic_task",
    "register_dynamic_task",
    "validate_bounded_loop",
    "validate_evidence_insufficient_replan",
    "validate_scheduling_decision",
]
