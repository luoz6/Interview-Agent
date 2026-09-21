from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.memory.agent_invocation_ledger import InMemoryAgentInvocationLedger
from app.adapters.persistence.postgres.agent_invocation_ledger import (
    PostgresAgentInvocationLedgerAdapter,
)
from app.application.scheduling import SchedulerApplicationCapability
from app.domain.agents.artifacts import DomainArtifact
from app.domain.execution_lease import LeaseBusy, LeaseLost
from app.domain.interview.scheduling import InvocationIdentity

from tests.contracts.test_scheduler_application_capability_contract import _fixture


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class RecordingStateStore:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.saved = []

    def load(self, execution_id):
        return self.delegate.load(execution_id)

    def save(self, state):
        self.saved.append(state)
        return self.delegate.save(state)


class NewWorkerInvoker:
    def __init__(self) -> None:
        self.requests = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.requests.append((agent_id, skill, request, execution_context))
        return DomainArtifact(
            artifact_type="followup-artifact",
            context_ref={"worker": "new"},
        )


class OldWorkerInvoker:
    def __init__(self, *, clock: Clock, new_scheduler) -> None:
        self.clock = clock
        self.new_scheduler = new_scheduler
        self.live_result = None
        self.reclaimed_result = None

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.live_result = self.new_scheduler.step("exec-1")
        self.clock.advance(11)
        self.reclaimed_result = self.new_scheduler.step("exec-1")
        return DomainArtifact(
            artifact_type="followup-artifact",
            context_ref={"worker": "old"},
        )


def test_stale_worker_cannot_publish_artifact_completion_or_observation():
    old_scheduler, _old_invoker, base_store = _fixture()
    clock = Clock()
    ledger = InMemoryAgentInvocationLedger(clock=clock)
    store = RecordingStateStore(base_store)
    new_invoker = NewWorkerInvoker()
    new_scheduler = SchedulerApplicationCapability(
        state_store=store,
        plan=old_scheduler.plan,
        capability_port=old_scheduler.capability_port,
        invocation_port=new_invoker,
        invocation_ledger=ledger,
        worker_id="worker-new",
        lease_seconds=10,
    )
    old_invoker = OldWorkerInvoker(clock=clock, new_scheduler=new_scheduler)
    old_scheduler.state_store = store
    old_scheduler.invocation_port = old_invoker
    old_scheduler.invocation_ledger = ledger
    old_scheduler.worker_id = "worker-old"
    old_scheduler.lease_seconds = 10

    old_result = old_scheduler.step("exec-1")

    assert old_invoker.live_result.action == "NOOP"
    assert isinstance(old_invoker.live_result.error, LeaseBusy)
    assert old_invoker.reclaimed_result.action == "DISPATCH"
    assert old_invoker.reclaimed_result.artifact.context_ref == {"worker": "new"}
    assert len(new_invoker.requests) == 1

    assert old_result.action == "NOOP"
    assert isinstance(old_result.error, LeaseLost)
    assert old_result.artifact is None
    assert old_result.observation is None

    final_state = store.load("exec-1")
    assert final_state.task_state("followup-1").status == "COMPLETED"
    assert final_state.latest_observation == old_invoker.reclaimed_result.observation
    assert len(final_state.artifact_refs) == 1
    identity = InvocationIdentity(
        execution_id="exec-1",
        task_id="followup-1",
        logical_attempt=1,
    )
    assert final_state.artifact_refs[0].artifact_ref == ledger.get(identity).artifact_ref
    assert [state.task_state("followup-1").status for state in store.saved] == [
        "RUNNING",
        "COMPLETED",
    ]


def test_postgres_lifecycle_updates_reject_a_lost_fence(monkeypatch):
    adapter = object.__new__(PostgresAgentInvocationLedgerAdapter)
    calls = []

    def reject_update(identity, **kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(adapter, "_update_status", reject_update)
    identity = InvocationIdentity(
        execution_id="exec-1",
        task_id="followup-1",
        logical_attempt=1,
    )

    with pytest.raises(LeaseLost, match="mark invocation running"):
        adapter.mark_running(
            identity,
            lease_owner="worker-old",
            fencing_version=1,
        )
    with pytest.raises(LeaseLost, match="fail invocation"):
        adapter.mark_failed(
            identity,
            error_result={"code": "provider_error"},
            lease_owner="worker-old",
            fencing_version=1,
        )

    assert calls[0]["predicate_values"] == ("worker-old", 1)
    assert "lease_owner=%s" in calls[0]["predicate"]
    assert "fencing_version=%s" in calls[0]["predicate"]
    assert calls[1]["predicate_values"] == ("worker-old", 1)
