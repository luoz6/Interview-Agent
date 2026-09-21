import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.adapters.memory import InMemoryAgentMemoryStore
from app.domain.context.token_estimation import ConservativeUtf8TokenEstimator
from app.domain.memory import (
    AgentMemoryContextPolicy,
    AgentMemoryRecord,
    AgentMemoryScope,
    select_agent_memory_context,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _scope() -> AgentMemoryScope:
    return AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id="session-a",
        agent_id="interview-reviewer",
        memory_type="reviewer",
    )


def _record(
    index: int,
    *,
    summary: str | None = None,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> AgentMemoryRecord:
    created = created_at or NOW
    return AgentMemoryRecord(
        memory_id=f"memory-{index}",
        summary=summary or f"summary {index}",
        created_at=created,
        expires_at=expires_at or created + timedelta(hours=1),
    )


def test_store_enforces_retrieval_limit_and_filters_expired_records():
    policy = AgentMemoryContextPolicy(retrieval_limit=2, max_ttl_seconds=3600)
    store = InMemoryAgentMemoryStore(policy=policy, clock=lambda: NOW)
    memory = store.bind(_scope())
    for index in range(3):
        memory.remember(scope=_scope(), memory=_record(index))

    assert [item.memory_id for item in memory.recall(scope=_scope())] == [
        "memory-1",
        "memory-2",
    ]
    assert len(memory.recall(scope=_scope(), limit=50)) == 2

    clock = [NOW - timedelta(hours=2)]
    expired_store = InMemoryAgentMemoryStore(policy=policy, clock=lambda: clock[0])
    expired_memory = expired_store.bind(_scope())
    expired_memory.remember(
        scope=_scope(),
        memory=_record(
            9,
            created_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(hours=1),
        ),
    )
    clock[0] = NOW
    assert expired_memory.recall(scope=_scope()) == ()


def test_store_rejects_expired_or_overlong_ttl_records():
    policy = AgentMemoryContextPolicy(max_ttl_seconds=60)
    memory = InMemoryAgentMemoryStore(policy=policy, clock=lambda: NOW).bind(_scope())

    with pytest.raises(ValueError, match="expired"):
        memory.remember(
            scope=_scope(),
            memory=_record(
                1,
                created_at=NOW - timedelta(minutes=2),
                expires_at=NOW - timedelta(minutes=1),
            ),
        )
    with pytest.raises(ValueError, match="maximum TTL"):
        memory.remember(
            scope=_scope(),
            memory=_record(2, expires_at=NOW + timedelta(minutes=2)),
        )


def test_context_selection_is_summary_only_expiry_aware_and_budget_bounded():
    policy = AgentMemoryContextPolicy(
        retrieval_limit=2,
        max_ttl_seconds=3600,
        max_context_tokens=32,
    )
    records = (
        _record(1, summary="older summary", created_at=NOW - timedelta(minutes=2)),
        _record(2, summary="newer summary with additional detail"),
        _record(
            3,
            summary="expired summary",
            created_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(hours=1),
        ),
    )

    selection = select_agent_memory_context(
        records,
        policy=policy,
        estimator=ConservativeUtf8TokenEstimator(),
        model="test-model",
        now=NOW,
    )

    assert selection.expired_count == 1
    assert len(selection.items) <= policy.retrieval_limit
    assert selection.estimated_tokens <= policy.max_context_tokens
    assert "expired summary" not in selection.rendered_context
    assert all(item.summary in selection.rendered_context for item in selection.items)


def test_memory_record_requires_summary_and_forbids_owned_payload_fields():
    with pytest.raises(ValidationError):
        AgentMemoryRecord(
            memory_id="memory-1",
            summary="",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )
    for field in ("raw_payload", "execution_state", "domain_artifact"):
        with pytest.raises(ValidationError, match=field):
            AgentMemoryRecord.model_validate(
                {
                    "memory_id": "memory-1",
                    "summary": "bounded summary",
                    "created_at": NOW,
                    "expires_at": NOW + timedelta(minutes=1),
                    field: {"owned_payload": "forbidden"},
                }
            )


def test_context_policy_reuses_existing_context_budget_primitives():
    path = ROOT / "app" / "domain" / "memory" / "agent_context.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_names = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }

    assert (
        "app.domain.context.selection",
        "truncate_text_to_tokens",
    ) in imported_names
    assert (
        "app.domain.context.token_estimation",
        "TokenEstimator",
    ) in imported_names
