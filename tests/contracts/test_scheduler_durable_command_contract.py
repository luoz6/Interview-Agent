from __future__ import annotations

from app.domain.interview.scheduling import UserCommand
from app.adapters.persistence.postgres.scheduler_commands import (
    PostgresSchedulerCommandAdapter,
)
from app.ports.scheduler_commands import SchedulerCommandPort

from tests.contracts.test_scheduler_wait_user_integration_contract import _scheduler


class RecordingPort:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return object()


class DurableLedger(RecordingPort):
    def __init__(self) -> None:
        super().__init__()
        self.records: dict[str, dict] = {}

    def enqueue(self, **kwargs):
        super().enqueue(**kwargs)
        self.records[kwargs["command_id"]] = {
            "command_id": kwargs["command_id"],
            "command_type": kwargs["command_type"],
            "expected_version": kwargs["expected_version"],
            "answer_text": kwargs["payload"].get("answer_text"),
        }

    def get(self, **kwargs):
        return self.records.get(kwargs["command_id"])


class CommandPayloadConflict(ValueError):
    pass


class ConflictingPort:
    def enqueue(self, **kwargs):
        raise CommandPayloadConflict(kwargs["command_id"])


def _waiting_scheduler(port):
    scheduler, store = _scheduler()
    scheduler.durable_command_port = port
    scheduler.step("exec-wait")
    waiting = scheduler.step("exec-wait")
    return scheduler, store, waiting.state.current_wait_handle


def _command(wait, *, payload=None):
    return UserCommand(
        command_id="durable-cmd-1",
        execution_id="exec-wait",
        wait_id=wait.wait_id,
        task_id=wait.task_id,
        question_id=wait.question_id,
        expected_revision=wait.issued_revision,
        payload=payload or {"answer_text": "bounded queue"},
    )


def test_scheduler_enqueues_fenced_command_before_state_transition():
    port = RecordingPort()
    scheduler, store, wait = _waiting_scheduler(port)
    command = _command(wait)

    accepted = scheduler.accept_user_command("exec-wait", command)

    assert accepted.outcome.disposition == "ACCEPT"
    assert port.calls == [
        {
            "execution_id": "exec-wait",
            "command_id": "durable-cmd-1",
            "command_type": "answer",
            "expected_version": wait.issued_revision,
            "payload": {"answer_text": "bounded queue"},
        }
    ]
    assert store.load("exec-wait") == accepted.state
    assert accepted.state.current_wait_handle is None


def test_durable_payload_conflict_does_not_advance_execution_state():
    scheduler, store, wait = _waiting_scheduler(ConflictingPort())
    before = store.load("exec-wait")

    result = scheduler.accept_user_command("exec-wait", _command(wait))

    assert result.outcome.disposition == "CONFLICT"
    assert result.outcome.reason_code == "command_payload_conflict"
    assert store.load("exec-wait") == before


def test_durable_replay_is_detected_when_local_command_ledger_is_empty():
    port = DurableLedger()
    scheduler, _store, wait = _waiting_scheduler(port)
    command = _command(wait)
    accepted = scheduler.accept_user_command("exec-wait", command)
    assert accepted.outcome.disposition == "ACCEPT"

    # Simulate a fresh application process with no in-memory command ledger.
    from app.application.scheduling import InMemoryUserCommandStore

    scheduler.command_store = InMemoryUserCommandStore()
    replay = scheduler.accept_user_command("exec-wait", command)

    assert replay.outcome.disposition == "REPLAY"
    assert replay.outcome.reason_code == "idempotent_replay"


def test_postgres_adapter_maps_neutral_command_to_existing_enqueue():
    class WorkflowStore:
        def __init__(self):
            self.kwargs = None

        def enqueue_command(self, **kwargs):
            self.kwargs = kwargs
            return "record"

        def get_command_or_none(self, session_id, command_id):
            return None

    store = WorkflowStore()
    adapter = PostgresSchedulerCommandAdapter(store)

    result = adapter.enqueue(
        execution_id="exec-1",
        command_id="cmd-1",
        command_type="answer",
        expected_version=7,
        payload={"answer_text": "answer"},
    )

    assert result == "record"
    assert store.kwargs == {
        "session_id": "exec-1",
        "command_id": "cmd-1",
        "command_type": "answer",
        "expected_version": 7,
        "answer_text": "answer",
    }

    assert PostgresSchedulerCommandAdapter(store).get(
        execution_id="exec-1", command_id="cmd-1"
    ) is None


def test_durable_port_is_a_runtime_checkable_neutral_protocol():
    assert isinstance(RecordingPort(), SchedulerCommandPort)
