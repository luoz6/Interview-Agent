from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.memory.agent_invocation_ledger import (
    InMemoryAgentInvocationLedger,
)
from app.domain.execution_lease import LeaseBusy, LeaseLost
from app.domain.interview.scheduling import (
    InvocationIdentity,
    InvocationLedgerEntry,
)
from app.ports.agent_invocation_ledger import AgentInvocationLedgerPort

from tests.contracts.test_scheduler_application_capability_contract import _fixture


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfter:
    def __init__(self, ledger, method):
        self.ledger = ledger
        self.method = method

    def __getattr__(self, name):
        target = getattr(self.ledger, name)
        if name != self.method:
            return target

        def crash_after(*args, **kwargs):
            target(*args, **kwargs)
            raise SimulatedProcessCrash(name)

        return crash_after


class CrashBeforeCommit:
    def __init__(self, ledger):
        self.ledger = ledger

    def __getattr__(self, name):
        if name == "commit":
            def crash_before_commit(*_args, **_kwargs):
                raise SimulatedProcessCrash(name)

            return crash_before_commit
        return getattr(self.ledger, name)


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def _with_ledger(ledger, *, worker_id, lease_seconds=30):
    scheduler, invoker, store = _fixture()
    scheduler.invocation_ledger = ledger
    scheduler.worker_id = worker_id
    scheduler.lease_seconds = lease_seconds
    return scheduler, invoker, store


def test_prepared_crash_is_redispatched_with_same_logical_identity():
    ledger = InMemoryAgentInvocationLedger()
    crashed, crashed_invoker, crashed_store = _with_ledger(
        CrashAfter(ledger, "prepare"),
        worker_id="worker-crashed",
    )

    with pytest.raises(SimulatedProcessCrash):
        crashed.step("exec-1")

    identity = InvocationIdentity(
        execution_id="exec-1",
        task_id="followup-1",
        logical_attempt=1,
    )
    assert ledger.get(identity).status == "PREPARED"
    assert crashed_store.load("exec-1").task_state("followup-1").status == "PENDING"
    assert crashed_invoker.requests == []

    recovered, recovered_invoker, _ = _with_ledger(
        ledger,
        worker_id="worker-recovered",
    )
    result = recovered.step("exec-1")

    assert result.action == "DISPATCH"
    assert result.state.task_state("followup-1").status == "COMPLETED"
    assert ledger.get(identity).status == "COMPLETED"
    assert len(recovered_invoker.requests) == 1


def test_running_crash_waits_for_expiry_then_reclaims_with_new_fence():
    clock = Clock()
    ledger = InMemoryAgentInvocationLedger(clock=clock)
    crashed, crashed_invoker, _ = _with_ledger(
        CrashAfter(ledger, "mark_running"),
        worker_id="worker-old",
        lease_seconds=30,
    )

    with pytest.raises(SimulatedProcessCrash):
        crashed.step("exec-1")

    identity = InvocationIdentity(
        execution_id="exec-1",
        task_id="followup-1",
        logical_attempt=1,
    )
    running = ledger.get(identity)
    assert running.status == "RUNNING"
    assert running.fencing_version == 1
    assert crashed_invoker.requests == []

    recovering, recovering_invoker, _ = _with_ledger(
        ledger,
        worker_id="worker-new",
        lease_seconds=30,
    )
    with pytest.raises(LeaseBusy):
        recovering.step("exec-1")
    assert recovering_invoker.requests == []

    clock.advance(31)
    result = recovering.step("exec-1")

    assert result.action == "DISPATCH"
    completed = ledger.get(identity)
    assert completed.status == "COMPLETED"
    assert completed.fencing_version == 2
    assert len(recovering_invoker.requests) == 1


def test_old_worker_cannot_commit_after_expiry_and_reclaim():
    clock = Clock()
    ledger = InMemoryAgentInvocationLedger(clock=clock)
    identity = InvocationIdentity(
        execution_id="exec-lease",
        task_id="task-1",
        logical_attempt=1,
    )
    ledger.prepare(
        InvocationLedgerEntry(
            identity=identity,
            agent_id="agent",
            skill="skill",
            request_digest="sha256:request",
        )
    )
    old = ledger.acquire(
        identity,
        owner_id="old",
        lease_seconds=10,
    )
    clock.advance(11)
    new = ledger.reclaim(
        identity,
        owner_id="new",
        lease_seconds=10,
    )

    assert new.fencing_version == old.fencing_version + 1
    with pytest.raises(LeaseLost):
        ledger.commit(
            identity,
            artifact_ref="artifact:old",
            lease_owner=old.owner_id,
            fencing_version=old.fencing_version,
        )

    receipt = ledger.commit(
        identity,
        artifact_ref="artifact:new",
        lease_owner=new.owner_id,
        fencing_version=new.fencing_version,
    )
    assert receipt.artifact_ref == "artifact:new"


def test_agent_result_before_receipt_crash_reclaims_one_committed_effect():
    clock = Clock()
    ledger = InMemoryAgentInvocationLedger(clock=clock)
    crashed, crashed_invoker, store = _with_ledger(
        CrashBeforeCommit(ledger),
        worker_id="worker-crashed",
        lease_seconds=30,
    )

    with pytest.raises(SimulatedProcessCrash):
        crashed.step("exec-1")

    identity = InvocationIdentity(
        execution_id="exec-1",
        task_id="followup-1",
        logical_attempt=1,
    )
    uncommitted = ledger.get(identity)
    assert len(crashed_invoker.requests) == 1
    assert uncommitted.status == "RUNNING"
    assert uncommitted.artifact_ref is None
    assert uncommitted.fencing_version == 1
    assert store.load("exec-1").task_state("followup-1").status == "RUNNING"

    recovered, recovered_invoker, _ = _with_ledger(
        ledger,
        worker_id="worker-recovered",
        lease_seconds=30,
    )
    recovered.state_store = store

    duplicate = recovered.step("exec-1")
    assert duplicate.action == "NOOP"
    assert isinstance(duplicate.error, LeaseBusy)
    assert recovered_invoker.requests == []

    clock.advance(31)
    result = recovered.step("exec-1")

    committed = ledger.get(identity)
    assert result.action == "DISPATCH"
    assert len(recovered_invoker.requests) == 1
    assert committed.status == "COMPLETED"
    assert committed.fencing_version == 2
    assert committed.artifact_ref == result.observation["artifact_ref"]
    assert store.load("exec-1").artifact_refs == result.state.artifact_refs
    assert len(result.state.artifact_refs) == 1


def test_committed_receipt_is_observed_after_crash_without_reinvocation():
    ledger = InMemoryAgentInvocationLedger()
    crashed, crashed_invoker, _ = _with_ledger(
        CrashAfter(ledger, "commit"),
        worker_id="worker-crashed",
    )

    with pytest.raises(SimulatedProcessCrash):
        crashed.step("exec-1")
    assert len(crashed_invoker.requests) == 1

    identity = InvocationIdentity(
        execution_id="exec-1",
        task_id="followup-1",
        logical_attempt=1,
    )
    committed = ledger.get(identity)
    assert committed.status == "COMPLETED"
    assert committed.artifact_ref is not None

    recovered, recovered_invoker, _ = _with_ledger(
        ledger,
        worker_id="worker-recovered",
    )
    result = recovered.step("exec-1")

    assert result.action == "DISPATCH"
    assert result.artifact is None
    assert result.observation["durable_replay"] is True
    assert result.observation["artifact_ref"] == committed.artifact_ref
    assert result.observation["interview_plan_ref"] == "plan-1"
    assert result.observation["execution_plan_revision"] == 1
    assert result.observation["execution_id"] == identity.execution_id
    assert result.observation["execution_state_revision"] == result.state.revision
    assert result.observation["task_id"] == identity.task_id
    assert result.observation["logical_invocation"] == identity.model_dump(
        mode="json"
    )
    assert result.observation["observation_ref"] == (
        f"{committed.artifact_ref}/observation"
    )
    assert result.state.task_state("followup-1").status == "COMPLETED"
    assert result.state.execution_status == "RUNNING"
    assert recovered_invoker.requests == []


def test_memory_recovery_ledger_implements_canonical_port():
    assert isinstance(InMemoryAgentInvocationLedger(), AgentInvocationLedgerPort)
