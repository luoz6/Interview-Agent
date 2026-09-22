"""Deterministic assembly of scheduler tasks into typed agent requests.

This module is deliberately a domain-only boundary.  A scheduler supplies a
task definition, the current execution state, and (optionally) the artifact
references visible to that task; it never supplies an arbitrary request dict
to an agent runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from app.domain.interview.scheduling.plan import ExecutionTaskDefinition
from app.domain.interview.scheduling.requests import (
    AgentRequest,
    REQUEST_CONTRACTS,
    parse_agent_request,
)
from app.domain.interview.scheduling.state import (
    ExecutionArtifactRef,
    ExecutionState,
)


class RequestAssemblyError(ValueError):
    """Raised when a task cannot be dispatched with its available inputs."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        task_id: str,
        missing: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.task_id = task_id
        self.missing = missing


def _missing(
    task: ExecutionTaskDefinition,
    *fields: str,
) -> RequestAssemblyError:
    names = tuple(field for field in fields if field)
    return RequestAssemblyError(
        f"missing_required_input for task {task.task_id}: "
        + ", ".join(names),
        code="missing_required_input",
        task_id=task.task_id,
        missing=names,
    )


def _parameters(task: ExecutionTaskDefinition) -> dict[str, Any]:
    """Copy parameters so assembly cannot mutate the immutable task object."""

    return dict(task.parameters)


def _state_snapshot(state: ExecutionState) -> dict[str, Any]:
    """Return the stable, bounded state view exposed to evaluation agents."""

    # model_dump preserves the aggregate's declared field order and excludes no
    # runtime facts by accident.  It is still a plain dict because the request
    # contracts intentionally model agent-facing state as a JSON-like object.
    return state.model_dump(mode="python")


def _artifact_refs(
    state: ExecutionState,
    artifact_refs: Iterable[ExecutionArtifactRef] | None,
) -> tuple[ExecutionArtifactRef, ...]:
    refs = tuple(state.artifact_refs if artifact_refs is None else artifact_refs)
    for ref in refs:
        if not isinstance(ref, ExecutionArtifactRef):
            raise TypeError("artifact_refs must contain ExecutionArtifactRef values")
    return refs


def _required_artifact_types(
    task: ExecutionTaskDefinition,
    skill: str,
) -> tuple[str, ...]:
    params = task.parameters
    declared = params.get("required_artifact_types", params.get("required_artifact_type", ()))
    if isinstance(declared, str):
        declared = (declared,)
    if declared is None:
        declared = ()
    if not isinstance(declared, (tuple, list, set, frozenset)):
        raise RequestAssemblyError(
            f"invalid required_artifact_types for task {task.task_id}",
            code="invalid_required_input_declaration",
            task_id=task.task_id,
        )
    required = tuple(str(item) for item in declared if str(item).strip())
    # Evaluation has no useful input without at least one produced artifact;
    # callers can narrow the accepted type through required_artifact_types.
    if not required and skill == "evaluate-answer":
        return ("*",)
    return required


def _validate_artifacts(
    task: ExecutionTaskDefinition,
    refs: tuple[ExecutionArtifactRef, ...],
    skill: str,
) -> None:
    required = _required_artifact_types(task, skill)
    if not required:
        return
    if not refs:
        raise _missing(task, "artifact_refs")
    if "*" in required:
        return
    available = {ref.artifact_type for ref in refs}
    missing = tuple(kind for kind in required if kind not in available)
    if missing:
        raise _missing(task, *(f"artifact_type:{kind}" for kind in missing))


def _artifact_payload(refs: tuple[ExecutionArtifactRef, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(ref.model_dump(mode="python") for ref in refs)


def _first(params: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value is not None:
            return value
    return default


def _assemble_payload(
    task: ExecutionTaskDefinition,
    state: ExecutionState,
    refs: tuple[ExecutionArtifactRef, ...],
) -> dict[str, Any]:
    params = _parameters(task)
    skill = task.skill
    state_snapshot = _state_snapshot(state)

    if skill == "generate-interview-plan":
        job_description = _first(params, "job_description", "jobDescription")
        resume_text = _first(params, "resume_text", "resumeText")
        missing = tuple(
            field
            for field, value in (
                ("job_description", job_description),
                ("resume_text", resume_text),
            )
            if not isinstance(value, str) or not value.strip()
        )
        if missing:
            raise _missing(task, *missing)
        return {
            "job_description": job_description,
            "resume_text": resume_text,
            "prep_run_id": _first(params, "prep_run_id", "prepRunId"),
            "configuration": params.get("configuration"),
            "knowledge_source_scope": params.get("knowledge_source_scope"),
        }

    if skill == "generate-main-question":
        intent = _first(params, "intent", default=state.latest_observation)
        if not isinstance(intent, dict) or not intent:
            raise _missing(task, "intent")
        return {
            "intent": intent,
            "conversation": tuple(_first(params, "conversation", default=()) or ()),
            "evidence": tuple(_first(params, "evidence", default=()) or ()),
            "timeout_seconds": _first(params, "timeout_seconds", "timeoutSeconds"),
        }

    if skill == "generate-followup":
        question_id = _first(params, "question_id", "questionId")
        if not isinstance(question_id, str) or not question_id.strip():
            wait = state.current_wait_handle
            question_id = wait.question_id if wait is not None else None
        if not isinstance(question_id, str) or not question_id.strip():
            raise _missing(task, "question_id")
        context = _first(params, "context", default=()) or ()
        return {
            "question_id": question_id,
            "context": tuple(context),
            "focus": _first(params, "focus", default=""),
            "gap_id": _first(params, "gap_id", "gapId"),
            "reason_code": _first(params, "reason_code", "reasonCode", default="gap"),
            "policy_version": _first(
                params, "policy_version", "policyVersion", default="adaptive_v1"
            ),
            "evidence_ids": tuple(
                _first(
                    params,
                    "evidence_ids",
                    "evidenceIds",
                    default=tuple(ref.artifact_ref for ref in refs),
                )
                or ()
            ),
        }

    if skill == "evaluate-answer":
        if "state" in params and params["state"] is None:
            raise _missing(task, "state")
        answer_state = _first(params, "state", default=state_snapshot)
        if not isinstance(answer_state, dict) or not answer_state:
            raise _missing(task, "state")
        return {
            "state": answer_state,
            "question_id": _first(params, "question_id", "questionId"),
            "answer_artifact_ref": next(
                (
                    ref.artifact_ref
                    for ref in reversed(refs)
                    if ref.artifact_type == "answer-artifact"
                ),
                None,
            ),
            "question_artifact_ref": next(
                (
                    ref.artifact_ref
                    for ref in reversed(refs)
                    if ref.artifact_type in {
                        "main-question-artifact",
                        "followup-artifact",
                    }
                ),
                None,
            ),
        }

    if skill == "evaluate-interview":
        if "state" in params and params["state"] is None:
            raise _missing(task, "state")
        interview_state = _first(params, "state", default=state_snapshot)
        if not isinstance(interview_state, dict) or not interview_state:
            raise _missing(task, "state")
        return {"state": interview_state}

    if skill == "generate-report":
        evaluation_items = _first(params, "evaluation_items", "evaluationItems")
        evaluation_artifacts = _first(
            params, "evaluation_artifacts", "evaluationArtifacts"
        )
        if evaluation_items is None and evaluation_artifacts is None:
            if refs:
                evaluation_artifacts = _artifact_payload(refs)
            else:
                raise _missing(task, "evaluation_items_or_evaluation_artifacts")
        plan = _first(params, "plan", default=state.latest_observation)
        if not isinstance(plan, dict) or not plan:
            raise _missing(task, "plan")
        session_id = _first(params, "session_id", "sessionId", default=state.execution_id)
        if not isinstance(session_id, str) or not session_id.strip():
            raise _missing(task, "session_id")
        return {
            "plan": plan,
            "session_id": session_id,
            "evaluation_items": (
                tuple(evaluation_items) if evaluation_items is not None else None
            ),
            "evaluation_artifacts": (
                tuple(evaluation_artifacts) if evaluation_artifacts is not None else None
            ),
            "question_text_by_id": dict(
                _first(params, "question_text_by_id", "questionTextById", default={})
                or {}
            ),
        }

    raise RequestAssemblyError(
        f"unsupported Agent skill: {skill}",
        code="unsupported_skill",
        task_id=task.task_id,
    )


def assemble_agent_request(
    task: ExecutionTaskDefinition,
    state: ExecutionState,
    artifact_refs: Iterable[ExecutionArtifactRef] | None = None,
) -> AgentRequest:
    """Assemble one deterministic typed request for ``task``.

    ``artifact_refs`` defaults to the references in ``state``.  The optional
    argument exists for schedulers that have already filtered the references to
    the task's declared visibility.  The returned value is always one of the
    concrete request models in :data:`REQUEST_CONTRACTS`, never a dict.
    """

    if not isinstance(state, ExecutionState):
        raise RequestAssemblyError(
            f"missing_required_input for task {task.task_id}: state",
            code="missing_required_input",
            task_id=task.task_id,
            missing=("state",),
        )
    if task.skill not in REQUEST_CONTRACTS:
        raise RequestAssemblyError(
            f"unsupported Agent skill: {task.skill}",
            code="unsupported_skill",
            task_id=task.task_id,
        )
    refs = _artifact_refs(state, artifact_refs)
    _validate_artifacts(task, refs, task.skill)
    payload = _assemble_payload(task, state, refs)
    try:
        return parse_agent_request(task.skill, payload)
    except ValidationError as exc:
        # Keep dispatch errors domain-specific and stable while retaining the
        # typed model's useful field paths in the message.
        fields = tuple(
            str(error.get("loc", ()))
            for error in exc.errors()
            if error.get("loc")
        )
        raise RequestAssemblyError(
            f"missing_required_input for task {task.task_id}: "
            + (", ".join(fields) or "request validation"),
            code="missing_required_input",
            task_id=task.task_id,
            missing=fields,
        ) from exc


__all__ = ["RequestAssemblyError", "assemble_agent_request"]
