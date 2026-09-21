import ast
from pathlib import Path

from app.a2a.cards import (
    EXAMINER_AGENT_CARD,
    KNOWLEDGE_AGENT_CARD,
    REPORT_COACH_AGENT_CARD,
    REVIEWER_AGENT_CARD,
)
from app.a2a.registry import AgentRegistry
from app.domain.interview.scheduling import CapabilityDescriptor
from app.ports.agent_capability import AgentCapabilityPort


ROOT = Path(__file__).resolve().parents[2]


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    for card in (
        EXAMINER_AGENT_CARD,
        KNOWLEDGE_AGENT_CARD,
        REVIEWER_AGENT_CARD,
        REPORT_COACH_AGENT_CARD,
    ):
        registry.register_card(card)
    return registry


def test_existing_a2a_registry_is_the_canonical_capability_port():
    registry = _registry()

    assert isinstance(registry, AgentCapabilityPort)
    capabilities = registry.list_capabilities()
    assert len(capabilities) == 6
    assert {
        (capability.agent_id, capability.skill)
        for capability in capabilities
    } == {
        ("interview-examiner", "generate-main-question"),
        ("interview-examiner", "generate-followup"),
        ("knowledge-and-grounding", "generate-interview-plan"),
        ("interview-reviewer", "evaluate-answer"),
        ("interview-reviewer", "evaluate-interview"),
        ("report-coach", "generate-report"),
    }


def test_registry_resolves_typed_contract_and_checks_required_inputs():
    registry = _registry()
    capability = registry.resolve(
        agent_id="report-coach",
        skill="generate-report",
    )

    assert capability is not None
    assert capability.request_contract_id == "generate-report-request"
    assert capability.request_contract_version == "v1"
    assert capability.output_artifact_type == "report-artifact"
    assert registry.validate_compatibility(
        agent_id="report-coach",
        skill="generate-report",
        request_contract_id="generate-report-request",
        request_contract_version="v1",
        input_artifact_types=(
            "interview-plan-artifact",
            "evaluation-artifact",
        ),
    )
    assert not registry.validate_compatibility(
        agent_id="report-coach",
        skill="generate-report",
        request_contract_id="generate-report-request",
        request_contract_version="v1",
        input_artifact_types=("interview-plan-artifact",),
    )


def test_registry_adapter_does_not_create_a_second_core_or_transport_registry():
    path = ROOT / "app" / "a2a" / "registry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.ports.agent_capability" in imported_modules
    assert "app.domain.interview.scheduling.capabilities" in imported_modules
    assert sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "AgentRegistry"
    ) == 1


def test_explicit_typed_descriptor_is_not_overwritten_by_card_defaults():
    explicit = CapabilityDescriptor(
        agent_id="interview-examiner",
        skill="generate-followup",
        description="Deployment-specific follow-up contract.",
        request_contract_id="generate-followup-request",
        request_contract_version="v2",
        output_artifact_type="followup-artifact",
        output_artifact_version="2.0",
        capability_version="v1",
    )
    registry = AgentRegistry(capabilities=[explicit])
    registry.register_card(EXAMINER_AGENT_CARD)

    assert registry.resolve(
        agent_id="interview-examiner",
        skill="generate-followup",
    ) == explicit
