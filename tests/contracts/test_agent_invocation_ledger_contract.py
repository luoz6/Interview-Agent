import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domain.interview.scheduling import (
    InvocationCommitReceipt,
    InvocationIdentity,
    InvocationLedgerEntry,
)
from app.domain.execution_lease import LeaseToken
from app.runtime.reliability import LeaseToken as RuntimeLeaseToken
from app.ports.agent_invocation_ledger import AgentInvocationLedgerPort


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _identity() -> InvocationIdentity:
    return InvocationIdentity(
        execution_id="exec-1",
        task_id="task-1",
        logical_attempt=2,
    )


def _entry(**changes) -> InvocationLedgerEntry:
    values = {
        "identity": _identity(),
        "agent_id": "interview-reviewer",
        "skill": "evaluate-answer",
        "request_digest": "sha256:request-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return InvocationLedgerEntry(**values)


def test_identity_is_stable_and_ledger_records_required_facts():
    identity = _identity()
    assert identity.key == ("exec-1", "task-1", 2)
    prepared = _entry()
    assert prepared.status == "PREPARED"
    assert prepared.identity_key == identity.key

    running = _entry(
        status="RUNNING",
        lease_owner="worker-1",
        fencing_version=3,
        started_at=NOW,
    )
    assert running.lease_owner == "worker-1"
    assert running.fencing_version == 3

    completed = _entry(
        status="COMPLETED",
        artifact_ref="artifact:evaluation:1",
        finished_at=NOW,
    )
    assert completed.artifact_ref == "artifact:evaluation:1"

    failed = _entry(
        status="FAILED",
        error_result={"code": "provider_timeout", "retryable": True},
        finished_at=NOW,
    )
    assert failed.error_result["code"] == "provider_timeout"


def test_terminal_statuses_require_their_result_and_running_requires_lease():
    with pytest.raises(ValueError, match="lease_owner"):
        _entry(status="RUNNING")
    with pytest.raises(ValueError, match="artifact_ref"):
        _entry(status="COMPLETED")
    with pytest.raises(ValueError, match="error_result"):
        _entry(status="FAILED")


def test_invocation_ledger_port_exposes_stable_lifecycle_methods():
    class FakeLedger:
        def get(self, identity):
            return None

        def prepare(self, entry):
            return entry

        def mark_running(self, identity, *, lease_owner, fencing_version):
            return _entry(status="RUNNING", lease_owner=lease_owner)

        def mark_completed(self, identity, *, artifact_ref, lease_owner, fencing_version):
            return _entry(status="COMPLETED", artifact_ref=artifact_ref)

        def mark_failed(self, identity, *, error_result, lease_owner, fencing_version):
            return _entry(status="FAILED", error_result=error_result)

        def commit(self, identity, *, artifact_ref, lease_owner, fencing_version):
            return InvocationCommitReceipt(
                identity=identity,
                artifact_ref=artifact_ref,
                fencing_version=fencing_version,
            )

        def acquire(self, identity, *, owner_id, lease_seconds, now=None):
            return LeaseToken(
                resource_id=": ".join(identity.key[:2]),
                owner_id=owner_id,
                token="token-1",
                fencing_version=1,
                expires_at=NOW,
            )

        def renew(self, identity, lease, *, lease_seconds, now=None):
            return lease

        def expire(self, identity, *, now=None):
            return True

        def reclaim(self, identity, *, owner_id, lease_seconds, now=None):
            return self.acquire(
                identity,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
                now=now,
            )

        def assert_lease(self, identity, lease, *, now=None):
            return None

        def delete_execution(self, execution_id):
            return 0

    ledger = FakeLedger()
    assert isinstance(ledger, AgentInvocationLedgerPort)
    assert ledger.get(_identity()) is None
    assert ledger.prepare(_entry()).identity_key == _identity().key


def test_ledger_contract_is_transport_neutral():
    for relative_path in (
        ("app", "domain", "interview", "scheduling", "ledger.py"),
        ("app", "ports", "agent_invocation_ledger.py"),
    ):
        tree = ast.parse((ROOT.joinpath(*relative_path)).read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert all(not module.startswith("app.a2a") for module in imported_modules)


def test_invocation_ledger_reuses_the_authoritative_execution_lease_capability():
    assert RuntimeLeaseToken is LeaseToken
    required_methods = {
        "acquire",
        "renew",
        "expire",
        "reclaim",
        "assert_lease",
    }
    assert required_methods.issubset(
        {
            name
            for name in dir(AgentInvocationLedgerPort)
            if not name.startswith("_")
        }
    )
