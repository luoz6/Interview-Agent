from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.application.scheduling import SchedulerApplicationCapability
from app.domain.agents.artifacts import DomainArtifact
from app.domain.execution_lease import LeaseToken
from app.domain.interview.scheduling import (
    InvocationCommitReceipt,
    InvocationIdentity,
    InvocationLedgerEntry,
)

from tests.contracts.test_scheduler_wait_user_integration_contract import _scheduler


class Ledger:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.entries: dict[tuple[str, str, int], InvocationLedgerEntry] = {}

    def get(self, identity):
        return self.entries.get(identity.key)

    def prepare(self, entry):
        self.events.append("prepare")
        self.entries.setdefault(entry.identity.key, entry)
        return self.entries[entry.identity.key]

    def acquire(self, identity, *, owner_id, lease_seconds, now=None):
        self.events.append("acquire")
        now = now or datetime.now(timezone.utc)
        lease = LeaseToken(
            resource_id=": ".join(identity.key[:2]),
            owner_id=owner_id,
            token="lease-token",
            fencing_version=1,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        current = self.entries[identity.key]
        self.entries[identity.key] = current.model_copy(
            update={
                "status": "RUNNING",
                "lease_owner": owner_id,
                "lease_token": lease.token,
                "lease_expires_at": lease.expires_at,
                "fencing_version": 1,
            }
        )
        return lease

    def mark_running(self, identity, *, lease_owner, fencing_version):
        self.events.append("mark_running")
        return self.entries[identity.key]

    def commit(self, identity, *, artifact_ref, lease_owner, fencing_version):
        self.events.append("commit")
        current = self.entries[identity.key]
        self.entries[identity.key] = current.model_copy(
            update={"status": "COMPLETED", "artifact_ref": artifact_ref}
        )
        return InvocationCommitReceipt(
            identity=identity,
            artifact_ref=artifact_ref,
            fencing_version=fencing_version,
        )

    def mark_failed(self, identity, *, error_result, lease_owner, fencing_version):
        self.events.append("mark_failed")
        current = self.entries[identity.key]
        self.entries[identity.key] = current.model_copy(
            update={"status": "FAILED", "error_result": error_result}
        )
        return self.entries[identity.key]


class EventInvoker:
    def __init__(self, events):
        self.events = events

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.events.append("invoke")
        return DomainArtifact(
            artifact_type="followup-artifact",
            schema_version="1.0",
        )


class EventStateStore:
    def __init__(self, delegate, events):
        self.delegate = delegate
        self.events = events

    def load(self, execution_id):
        return self.delegate.load(execution_id)

    def save(self, state):
        self.events.append(f"state_save:{state.task_states[0].status}")
        return self.delegate.save(state)


def test_scheduler_dispatch_uses_durable_ledger_lease_and_commit_order():
    scheduler, _store = _scheduler()
    events: list[str] = []
    scheduler.invocation_port = EventInvoker(events)
    ledger = Ledger(events)
    scheduler.invocation_ledger = ledger

    result = scheduler.step("exec-wait")

    assert result.action == "DISPATCH"
    assert result.artifact is not None
    assert events == ["prepare", "acquire", "mark_running", "invoke", "commit"]
    identity = next(iter(ledger.entries))
    assert ledger.entries[identity].status == "COMPLETED"
    assert ledger.entries[identity].artifact_ref == result.observation["artifact_ref"]


def test_durable_ledger_receives_failure_when_invocation_fails():
    scheduler, _store = _scheduler()
    events: list[str] = []
    ledger = Ledger(events)
    scheduler.invocation_ledger = ledger

    class FailingInvoker(EventInvoker):
        def invoke(self, **kwargs):
            self.events.append("invoke")
            raise RuntimeError("provider down")

    scheduler.invocation_port = FailingInvoker(events)
    result = scheduler.step("exec-wait")

    assert result.action == "FAILED"
    assert events == ["prepare", "acquire", "mark_running", "invoke", "mark_failed"]
    identity = next(iter(ledger.entries))
    assert ledger.entries[identity].status == "FAILED"


def test_observation_and_task_completion_save_after_ledger_commit():
    scheduler, store = _scheduler()
    events: list[str] = []
    scheduler.state_store = EventStateStore(store, events)
    scheduler.invocation_port = EventInvoker(events)
    scheduler.invocation_ledger = Ledger(events)

    result = scheduler.step("exec-wait")

    assert result.action == "DISPATCH"
    assert events.index("commit") < events.index("state_save:COMPLETED")
