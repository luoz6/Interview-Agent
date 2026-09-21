import ast
from pathlib import Path

from app.domain.interview.scheduling import CapabilityDescriptor
from app.ports.agent_capability import AgentCapabilityPort


ROOT = Path(__file__).resolve().parents[2]


def _capability(version: str = "v1") -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Evaluate one answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="v1",
        required_input_artifact_types=("answer-artifact",),
        capability_version=version,
    )


class FakeCapabilityCatalog:
    def __init__(self, *capabilities: CapabilityDescriptor) -> None:
        self._capabilities = tuple(capabilities)

    def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return self._capabilities

    def resolve(
        self,
        *,
        agent_id: str,
        skill: str,
        capability_version: str | None = None,
    ) -> CapabilityDescriptor | None:
        matches = tuple(
            capability
            for capability in self._capabilities
            if capability.agent_id == agent_id
            and capability.skill == skill
            and (
                capability_version is None
                or capability.capability_version == capability_version
            )
        )
        return matches[0] if len(matches) == 1 else None

    def validate_compatibility(
        self,
        *,
        agent_id: str,
        skill: str,
        request_contract_id: str,
        request_contract_version: str,
        input_artifact_types=(),
        capability_version: str | None = None,
    ) -> bool:
        capability = self.resolve(
            agent_id=agent_id,
            skill=skill,
            capability_version=capability_version,
        )
        return capability is not None and capability.is_compatible(
            agent_id=agent_id,
            skill=skill,
            request_contract_id=request_contract_id,
            request_contract_version=request_contract_version,
            input_artifact_types=input_artifact_types,
        )


def test_capability_port_exposes_minimal_lookup_and_validation_contract():
    catalog = FakeCapabilityCatalog(_capability())
    assert isinstance(catalog, AgentCapabilityPort)
    assert catalog.list_capabilities() == (_capability(),)
    assert catalog.resolve(
        agent_id="interview-reviewer", skill="evaluate-answer"
    ) == _capability()
    assert catalog.resolve(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        capability_version="v2",
    ) is None
    assert catalog.validate_compatibility(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        input_artifact_types=("answer-artifact",),
    )
    assert not catalog.validate_compatibility(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        input_artifact_types=(),
    )


def test_capability_port_is_transport_neutral():
    path = ROOT / "app" / "ports" / "agent_capability.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(not module.startswith("app.a2a") for module in imported_modules)
    assert "app.domain.interview.scheduling.capabilities" in imported_modules
