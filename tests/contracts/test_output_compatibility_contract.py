import ast
from pathlib import Path

import pytest

from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionArtifactRef,
    ExecutionState,
    OutputCompatibilityError,
    record_compatible_artifact,
    validate_output_compatibility,
)


ROOT = Path(__file__).resolve().parents[2]


def _capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Evaluate one answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="v1",
        capability_version="v1",
    )


def _artifact(**changes) -> DomainArtifact:
    values = {
        "artifact_type": "evaluation-artifact",
        "schema_version": "v1",
    }
    values.update(changes)
    return DomainArtifact(**values)


def test_output_type_and_version_must_match_capability_exactly():
    capability = _capability()
    assert capability.is_output_compatible(
        artifact_type="evaluation-artifact", artifact_version="v1"
    )
    assert not capability.is_output_compatible(
        artifact_type="evaluation-artifact", artifact_version="v2"
    )
    validate_output_compatibility(capability, _artifact())

    with pytest.raises(OutputCompatibilityError) as wrong_type:
        validate_output_compatibility(
            capability,
            _artifact(artifact_type="followup-artifact"),
        )
    assert wrong_type.value.actual_type == "followup-artifact"
    assert wrong_type.value.expected_version == "v1"

    with pytest.raises(OutputCompatibilityError) as wrong_version:
        validate_output_compatibility(
            capability,
            _artifact(schema_version="v2"),
        )
    assert wrong_version.value.actual_version == "v2"


def test_missing_output_identity_is_rejected_before_state_write():
    capability = _capability()
    with pytest.raises(OutputCompatibilityError, match="incompatible"):
        validate_output_compatibility(capability, {"artifact_type": "evaluation-artifact"})

    state = ExecutionState(execution_id="exec-1")
    with pytest.raises(OutputCompatibilityError):
        record_compatible_artifact(
            state,
            capability,
            _artifact(schema_version="v2"),
            artifact_ref="artifact:bad",
        )
    assert state.revision == 0
    assert state.artifact_refs == ()


def test_compatible_output_is_the_only_path_that_enters_scheduler_state():
    state = ExecutionState(execution_id="exec-1")
    next_state = record_compatible_artifact(
        state,
        _capability(),
        _artifact(),
        artifact_ref="artifact:evaluation:1",
        task_id="evaluate-answer-1",
    )

    assert state.revision == 0
    assert next_state.revision == 1
    assert len(next_state.artifact_refs) == 1
    assert next_state.artifact_refs[0] == ExecutionArtifactRef(
        artifact_ref="artifact:evaluation:1",
        artifact_type="evaluation-artifact",
        artifact_version="v1",
        task_id="evaluate-answer-1",
    )


def test_artifact_ref_carries_version_and_accepts_schema_version_alias():
    ref = ExecutionArtifactRef(
        artifact_ref="artifact:evaluation:1",
        artifact_type="evaluation-artifact",
        schema_version="v1",
    )
    assert ref.artifact_version == "v1"
    assert ref.schema_version == "v1"


def test_compatibility_gate_is_pure_domain():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "compatibility.py"
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
