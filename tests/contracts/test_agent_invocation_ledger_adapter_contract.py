import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.adapters.persistence.postgres.agent_invocation_ledger import (
    PostgresAgentInvocationLedgerAdapter,
)
from app.domain.interview.scheduling import (
    InvocationIdentity,
    InvocationLedgerEntry,
)
from app.domain.execution_lease import LeaseLost


ROOT = Path(__file__).resolve().parents[2]


def test_postgres_adapter_implements_the_durable_ledger_surface():
    expected = {
        "get",
        "prepare",
        "mark_running",
        "mark_completed",
        "commit",
        "mark_failed",
        "acquire",
        "renew",
        "expire",
        "reclaim",
        "assert_lease",
        "delete_execution",
    }
    assert expected.issubset(
        {
            name
            for name, value in inspect.getmembers(
                PostgresAgentInvocationLedgerAdapter,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }
    )


def test_adapter_uses_existing_connection_provider_and_runtime_table_not_new_db_subsystem():
    source = (
        ROOT
        / "app"
        / "adapters"
        / "persistence"
        / "postgres"
        / "agent_invocation_ledger.py"
    ).read_text(encoding="utf-8")
    assert "ConnectionProvider" in source
    assert "postgres_sql" in source
    assert "CREATE DATABASE" not in source.upper()
    assert "sqlite" not in source.lower()


def test_runtime_schema_declares_invocation_ledger_relation_and_fencing_columns():
    schema_source = (
        ROOT / "app" / "adapters" / "postgres" / "store_schema_adapter.py"
    ).read_text(encoding="utf-8")
    assert "agent_invocations" in schema_source
    assert "logical_attempt" in schema_source
    assert "request_digest" in schema_source
    assert "fencing_version" in schema_source
    assert "lease_expires_at" in schema_source


def test_adapter_and_contracts_remain_transport_neutral():
    path = (
        ROOT
        / "app"
        / "adapters"
        / "persistence"
        / "postgres"
        / "agent_invocation_ledger.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.a2a" not in " ".join(imported_modules)


def test_identity_is_the_database_primary_key_shape():
    identity = InvocationIdentity(
        execution_id="exec-1", task_id="task-1", logical_attempt=1
    )
    entry = InvocationLedgerEntry(
        identity=identity,
        agent_id="reviewer",
        skill="evaluate-answer",
        request_digest="sha256:req",
    )
    assert entry.identity.key == ("exec-1", "task-1", 1)


def test_postgres_adapter_cannot_reacquire_a_completed_invocation(monkeypatch):
    adapter = object.__new__(PostgresAgentInvocationLedgerAdapter)
    identity = InvocationIdentity(
        execution_id="exec-1", task_id="task-1", logical_attempt=1
    )
    now = datetime.now(timezone.utc)
    completed = InvocationLedgerEntry(
        identity=identity,
        status="COMPLETED",
        agent_id="reviewer",
        skill="evaluate-answer",
        request_digest="sha256:req",
        artifact_ref="artifact:committed",
        fencing_version=3,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    monkeypatch.setattr(adapter, "_require", lambda _identity: completed)

    with pytest.raises(LeaseLost, match="completed invocation"):
        adapter.acquire(identity, owner_id="worker-new", lease_seconds=30)


def test_postgres_lease_mutations_are_expiry_and_cas_fenced():
    source = (
        ROOT
        / "app"
        / "adapters"
        / "persistence"
        / "postgres"
        / "agent_invocation_ledger.py"
    ).read_text(encoding="utf-8")

    assert "AND lease_expires_at>NOW() RETURNING finished_at" in source
    assert source.count("AND lease_expires_at>NOW()") >= 3
    assert "AND fencing_version=%s " in source
    assert "AND (lease_expires_at IS NULL OR lease_expires_at<=%s)" in source
    assert "RETURNING fencing_version" in source
    assert "invocation lease acquisition lost its fencing race" in source
    assert "invocation lease renewal lost its fencing race" in source
