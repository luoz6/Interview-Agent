import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.adapters.memory import InMemoryAgentMemoryStore
from app.domain.agents import DomainArtifact
from app.domain.memory import (
    AgentMemoryAccessDenied,
    AgentMemoryRecord,
    AgentMemoryScope,
)
from app.ports import AgentMemoryPort


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _scope(agent_id: str, memory_type: str) -> AgentMemoryScope:
    return AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id="session-a",
        agent_id=agent_id,
        memory_type=memory_type,
    )


def _memory(memory_id: str, summary: str) -> AgentMemoryRecord:
    return AgentMemoryRecord(
        memory_id=memory_id,
        summary=summary,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def test_reviewer_cannot_recall_examiner_private_memory():
    store = InMemoryAgentMemoryStore(clock=lambda: NOW)
    examiner_scope = _scope("interview-examiner", "examiner")
    reviewer_scope = _scope("interview-reviewer", "reviewer")
    examiner_memory = store.bind(examiner_scope)
    reviewer_memory = store.bind(reviewer_scope)

    examiner_memory.remember(
        scope=examiner_scope,
        memory=_memory("examiner-strategy", "probe consistency tradeoffs"),
    )

    assert isinstance(examiner_memory, AgentMemoryPort)
    assert isinstance(reviewer_memory, AgentMemoryPort)
    with pytest.raises(AgentMemoryAccessDenied):
        reviewer_memory.recall(scope=examiner_scope)
    assert reviewer_memory.recall(scope=reviewer_scope) == ()
    assert examiner_memory.recall(scope=examiner_scope) == (
        _memory("examiner-strategy", "probe consistency tradeoffs"),
    )


def test_every_ownership_dimension_isolated_in_one_physical_store():
    owner = _scope("interview-reviewer", "reviewer")
    owner_memory = InMemoryAgentMemoryStore(clock=lambda: NOW).bind(owner)
    changes = {
        "deployment_id": "deployment-b",
        "principal_id": "principal-b",
        "session_id": "session-b",
        "agent_id": "interview-examiner",
        "memory_type": "examiner",
    }

    for field, value in changes.items():
        foreign_scope = AgentMemoryScope.model_validate(
            {**owner.model_dump(), field: value}
        )
        with pytest.raises(AgentMemoryAccessDenied):
            owner_memory.recall(scope=foreign_scope)


def test_cross_agent_exchange_contract_returns_domain_artifact():
    path = ROOT / "app" / "ports" / "agent_invocation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    invoke = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "invoke"
    )

    assert ast.unparse(invoke.returns) == "DomainArtifact"
    assert DomainArtifact(artifact_type="evaluation-artifact").artifact_type == (
        "evaluation-artifact"
    )
