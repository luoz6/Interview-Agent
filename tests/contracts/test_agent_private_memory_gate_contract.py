import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.adapters.memory import InMemoryAgentMemoryStore
from app.domain.memory import (
    AGENT_MEMORY_TYPES,
    AgentMemoryAccessDenied,
    AgentMemoryRecord,
    AgentMemoryScope,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _scope(memory_type: str) -> AgentMemoryScope:
    return AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id="session-a",
        agent_id=f"{memory_type}-agent",
        memory_type=memory_type,
    )


def _record(memory_type: str) -> AgentMemoryRecord:
    return AgentMemoryRecord(
        memory_id=f"{memory_type}-memory",
        summary=f"bounded {memory_type} strategy summary",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def test_all_five_namespaces_share_one_store_and_remain_isolated():
    store = InMemoryAgentMemoryStore(clock=lambda: NOW)
    scoped_ports = {memory_type: store.bind(_scope(memory_type)) for memory_type in AGENT_MEMORY_TYPES}

    for memory_type, port in scoped_ports.items():
        port.remember(scope=_scope(memory_type), memory=_record(memory_type))

    for memory_type, port in scoped_ports.items():
        assert port.recall(scope=_scope(memory_type)) == (_record(memory_type),)
        foreign_type = next(item for item in AGENT_MEMORY_TYPES if item != memory_type)
        with pytest.raises(AgentMemoryAccessDenied):
            port.recall(scope=_scope(foreign_type))


def test_agent_memory_has_one_physical_store_implementation():
    implementations = []
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        implementations.extend(
            (path, node.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name.endswith("AgentMemoryStore")
        )

    assert [(path.name, name) for path, name in implementations] == [
        ("agent_memory.py", "InMemoryAgentMemoryStore")
    ]


def test_professional_agents_do_not_persist_unproven_private_memory():
    forbidden_imports = {
        "app.ports.agent_memory",
        "app.adapters.memory.agent_memory",
    }
    for path in (ROOT / "app" / "agents").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not (imported_modules & forbidden_imports), path
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "remember"
            for node in ast.walk(tree)
        ), path
