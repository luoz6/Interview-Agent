import ast
from pathlib import Path

import pytest

from app.domain.interview.scheduling import (
    ExecutionArtifactRef,
    ExecutionState,
    ExecutionTaskDefinition,
    EvaluateAnswerRequest,
    GenerateFollowupRequest,
    GenerateReportRequest,
    RequestAssemblyError,
    WaitHandle,
    assemble_agent_request,
)


ROOT = Path(__file__).resolve().parents[2]


def _task(skill: str, **parameters):
    return ExecutionTaskDefinition(
        task_id=f"task-{skill}",
        capability=f"interview.{skill}",
        agent_id="interview-agent",
        skill=skill,
        parameters=parameters,
    )


def test_followup_assembly_is_typed_and_deterministic_from_task_and_state():
    state = ExecutionState(
        execution_id="exec-1",
        current_wait_handle=WaitHandle(
            wait_id="wait-1",
            execution_id="exec-1",
            task_id="task-generate-followup",
            question_id="q-1",
            issued_revision=0,
            expected_command_kind="answer",
        ),
        latest_observation={"focus": "tradeoffs"},
    )
    task = _task(
        "generate-followup",
        context=[{"role": "candidate", "content": "I used caching."}],
        focus="tradeoffs",
    )

    first = assemble_agent_request(task, state)
    second = assemble_agent_request(task, state)

    assert isinstance(first, GenerateFollowupRequest)
    assert first == second
    assert first.question_id == "q-1"
    assert first.focus == "tradeoffs"


def test_evaluate_answer_requires_an_artifact_reference():
    task = _task("evaluate-answer")
    with pytest.raises(RequestAssemblyError, match="artifact_refs") as error:
        assemble_agent_request(task, ExecutionState(execution_id="exec-1"))
    assert error.value.code == "missing_required_input"


def test_evaluate_answer_uses_bounded_state_snapshot_and_artifact():
    state = ExecutionState(
        execution_id="exec-1",
        latest_observation={"answer": "I reduced latency."},
        artifact_refs=(
            ExecutionArtifactRef(
                artifact_ref="artifact:answer:1",
                artifact_type="answer",
                task_id="answer-1",
            ),
        ),
    )
    request = assemble_agent_request(_task("evaluate-answer"), state)
    assert isinstance(request, EvaluateAnswerRequest)
    assert request.state["execution_id"] == "exec-1"
    assert request.state["artifact_refs"][0]["artifact_ref"] == "artifact:answer:1"


def test_report_rejects_missing_evaluation_input_and_accepts_refs():
    task = _task("generate-report", plan={"schema_version": "interview-plan-v2"})
    with pytest.raises(RequestAssemblyError, match="evaluation_items_or_evaluation_artifacts"):
        assemble_agent_request(task, ExecutionState(execution_id="session-1"))

    state = ExecutionState(
        execution_id="session-1",
        artifact_refs=(
            ExecutionArtifactRef(
                artifact_ref="artifact:evaluation:1",
                artifact_type="evaluation",
                task_id="evaluate-1",
            ),
        ),
    )
    request = assemble_agent_request(task, state)
    assert isinstance(request, GenerateReportRequest)
    assert request.session_id == "session-1"
    assert request.evaluation_artifacts[0]["artifact_ref"] == "artifact:evaluation:1"


def test_unknown_skill_and_missing_declared_artifact_are_rejected_before_dispatch():
    unknown = _task("not-a-real-skill")
    with pytest.raises(RequestAssemblyError) as error:
        assemble_agent_request(unknown, ExecutionState(execution_id="exec-1"))
    assert error.value.code == "unsupported_skill"

    task = _task(
        "evaluate-answer",
        required_artifact_types=("answer", "transcript"),
    )
    state = ExecutionState(
        execution_id="exec-1",
        artifact_refs=(
            ExecutionArtifactRef(
                artifact_ref="artifact:answer:1",
                artifact_type="answer",
            ),
        ),
    )
    with pytest.raises(RequestAssemblyError, match="artifact_type:transcript"):
        assemble_agent_request(task, state)


def test_assembler_is_pure_domain_and_returns_no_a2a_or_runtime_types():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "assembler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        not module.startswith(("app.adapters", "app.a2a", "app.runtime"))
        for module in imported_modules
    )
