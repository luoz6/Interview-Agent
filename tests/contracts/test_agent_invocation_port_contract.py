import ast
from pathlib import Path

from app.domain.agent_execution import AgentExecutionContext
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling import GenerateFollowupRequest
from app.ports.agent_invocation import AgentInvocationPort


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_invocation_port_is_neutral_and_typed():
    path = ROOT / "app" / "ports" / "agent_invocation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(not module.startswith("app.a2a") for module in imported_modules)
    assert "app.domain.agent_execution" in imported_modules
    assert "app.domain.agents.artifacts" in imported_modules
    assert "app.domain.interview.scheduling.requests" in imported_modules

    invoke = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "invoke"
    )
    request_argument = next(arg for arg in invoke.args.kwonlyargs if arg.arg == "request")
    assert ast.unparse(request_argument.annotation) == "AgentRequest"
    assert ast.unparse(invoke.returns) == "DomainArtifact"


def test_legacy_a2a_import_path_reexports_the_canonical_port():
    from app.a2a.invocation.port import AgentInvocationPort as LegacyPort

    assert LegacyPort is AgentInvocationPort


def test_port_is_runtime_checkable_for_neutral_agent_adapters():
    class FakeInvoker:
        def invoke(
            self,
            *,
            agent_id: str,
            skill: str,
            request: GenerateFollowupRequest,
            execution_context: AgentExecutionContext | None = None,
        ) -> DomainArtifact:
            return DomainArtifact(artifact_type="followup-artifact")

    assert isinstance(FakeInvoker(), AgentInvocationPort)
