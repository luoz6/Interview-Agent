import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.memory import AGENT_MEMORY_TYPES, AgentMemoryScope
from app.ports.agent_memory import AgentMemoryPort


ROOT = Path(__file__).resolve().parents[2]


class FakeAgentMemory:
    def __init__(self):
        self.entries = {}

    def recall(self, *, scope, query=None, limit=None):
        values = tuple(self.entries.get(scope, ()))
        return values if limit is None else values[:limit]

    def remember(self, *, scope, memory):
        self.entries.setdefault(scope, []).append(memory)
        return memory

    def delete_scope(self, *, scope):
        return len(self.entries.pop(scope, ()))


def test_agent_memory_port_exposes_minimal_recall_remember_and_delete_scope():
    memory = FakeAgentMemory()
    scope = AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id="session-a",
        agent_id="reviewer",
        memory_type="reviewer",
    )
    assert isinstance(memory, AgentMemoryPort)
    assert memory.remember(scope=scope, memory={"kind": "note"}) == {
        "kind": "note"
    }
    assert memory.recall(scope=scope) == (({"kind": "note"}),)
    assert memory.delete_scope(scope=scope) == 1
    assert memory.recall(scope=scope) == ()


def test_delete_scope_is_required_for_runtime_protocol_conformance():
    class MissingDeleteScope:
        def recall(self, *, scope, query=None, limit=None):
            return ()

        def remember(self, *, scope, memory):
            return memory

    assert not isinstance(MissingDeleteScope(), AgentMemoryPort)


def test_agent_memory_port_is_transport_neutral():
    path = ROOT / "app" / "ports" / "agent_memory.py"
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
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert {"recall", "remember", "delete_scope"} <= methods


def test_agent_memory_scope_requires_all_ownership_dimensions():
    scope = AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id="session-a",
        agent_id="reviewer",
        memory_type="reviewer",
    )
    assert set(scope.model_dump()) == {
        "deployment_id",
        "principal_id",
        "session_id",
        "agent_id",
        "memory_type",
    }
    assert scope != scope.model_copy(update={"agent_id": "examiner"})


@pytest.mark.parametrize("memory_type", AGENT_MEMORY_TYPES)
def test_five_logical_memory_namespaces_share_one_port(memory_type):
    memory = FakeAgentMemory()
    scope = AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id="session-a",
        agent_id=f"{memory_type}-agent",
        memory_type=memory_type,
    )

    memory.remember(scope=scope, memory={"namespace": memory_type})

    assert isinstance(memory, AgentMemoryPort)
    assert memory.recall(scope=scope) == ({"namespace": memory_type},)


def test_unknown_logical_memory_namespace_is_rejected():
    assert set(AGENT_MEMORY_TYPES) == {
        "scheduler",
        "knowledge",
        "examiner",
        "reviewer",
        "report-coach",
    }
    with pytest.raises(ValidationError, match="memory_type"):
        AgentMemoryScope(
            deployment_id="deployment-a",
            principal_id="principal-a",
            session_id="session-a",
            agent_id="other-agent",
            memory_type="other",
        )
