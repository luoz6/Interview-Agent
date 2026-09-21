import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import CapabilityDescriptor


ROOT = Path(__file__).resolve().parents[2]


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Evaluate one interview answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="v1",
        required_input_artifact_types=("answer-artifact",),
        capability_version="v1",
    )


def test_capability_descriptor_requires_typed_contract_identity():
    descriptor = _descriptor()
    assert descriptor.capability_key == "interview-reviewer:evaluate-answer:v1"
    assert descriptor.is_compatible(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        input_artifact_types=("answer-artifact",),
    )
    assert not descriptor.is_compatible(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        request_contract_id="wrong-contract",
        request_contract_version="v1",
        input_artifact_types=("answer-artifact",),
    )


def test_capability_descriptor_rejects_duplicate_or_missing_contract_fields():
    with pytest.raises(ValidationError):
        CapabilityDescriptor(
            agent_id="agent",
            skill="skill",
            description="description",
            request_contract_id="request-v1",
            request_contract_version="v1",
            output_artifact_type="artifact",
            output_artifact_version="v1",
            required_input_artifact_types=("answer", "answer"),
            capability_version="v1",
        )
    with pytest.raises(ValidationError):
        CapabilityDescriptor(
            agent_id="agent",
            skill="skill",
            description="description",
            request_contract_id="",
            request_contract_version="v1",
            output_artifact_type="artifact",
            output_artifact_version="v1",
            capability_version="v1",
        )


def test_capability_descriptor_is_pure_domain():
    path = (
        ROOT
        / "app"
        / "domain"
        / "interview"
        / "scheduling"
        / "capabilities.py"
    )
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
