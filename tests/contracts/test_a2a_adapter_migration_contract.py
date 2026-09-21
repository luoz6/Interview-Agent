import ast
from pathlib import Path

from app.a2a.invocation.a2a import A2AAgentInvoker
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling.requests import GenerateFollowupRequest
from app.ports.agent_invocation import AgentInvocationPort


ROOT = Path(__file__).resolve().parents[2]


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def send_task(self, **kwargs):
        self.calls.append(kwargs)
        return DomainArtifact(artifact_type="followup-artifact")


def test_a2a_invoker_implements_canonical_port_and_serializes_at_adapter_boundary():
    client = FakeClient()
    invoker = A2AAgentInvoker(client=client)
    request = GenerateFollowupRequest(question_id="q1", focus="tradeoffs")

    result = invoker.invoke(
        agent_id="interview-examiner",
        skill="generate-followup",
        request=request,
    )

    assert A2AAgentInvoker.port_type is AgentInvocationPort
    assert isinstance(invoker, AgentInvocationPort)
    assert result.artifact_type == "followup-artifact"
    assert client.calls[0]["request"]["question_id"] == "q1"
    assert isinstance(client.calls[0]["request"], dict)


def test_legacy_dict_is_only_accepted_as_a2a_adapter_compatibility_input():
    client = FakeClient()
    A2AAgentInvoker(client=client).invoke(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1"},
    )
    assert client.calls[0]["request"] == {"question_id": "q1"}


def test_a2a_invoker_keeps_transport_dependencies_inside_adapter_module():
    path = ROOT / "app" / "a2a" / "invocation" / "a2a.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.a2a.client" in imported_modules
    assert "app.ports.agent_invocation" in imported_modules
    assert "app.domain.agents.artifacts" in imported_modules
