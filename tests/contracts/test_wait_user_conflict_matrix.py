from __future__ import annotations

import pytest

from app.domain.interview.scheduling import UserCommand

from tests.contracts.test_scheduler_wait_user_integration_contract import _scheduler


class RecordingCommandPort:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, **values):
        self.calls.append(values)
        return values

    def get(self, *, execution_id, command_id):
        return None


def _waiting_scheduler():
    scheduler, store = _scheduler()
    commands = RecordingCommandPort()
    scheduler.durable_command_port = commands
    scheduler.step("exec-wait")
    waiting = scheduler.step("exec-wait").state
    assert waiting.current_wait_handle is not None
    return scheduler, store, commands, waiting


def _command(wait, **changes) -> UserCommand:
    values = {
        "command_id": "matrix-command",
        "execution_id": wait.execution_id,
        "wait_id": wait.wait_id,
        "task_id": wait.task_id,
        "question_id": wait.question_id,
        "expected_revision": wait.issued_revision,
        "payload": {"answer_text": "Use a revision-fenced write."},
    }
    values.update(changes)
    return UserCommand(**values)


@pytest.mark.parametrize(
    ("case", "changes", "reason_code"),
    (
        ("stale", {}, "stale_revision"),
        ("late", {"task_id": "old-question-task"}, "late_task"),
        ("wrong_wait", {"wait_id": "old-wait"}, "wrong_wait"),
        (
            "wrong_question",
            {"question_id": "old-question"},
            "wrong_question",
        ),
        ("wrong_revision", {"expected_revision": 999}, "stale_revision"),
    ),
)
def test_wait_user_invalid_command_matrix_is_fenced_without_side_effects(
    case,
    changes,
    reason_code,
):
    scheduler, store, commands, waiting = _waiting_scheduler()
    wait = waiting.current_wait_handle

    if case == "stale":
        waiting = waiting.apply_transition(
            expected_revision=waiting.revision,
            transition_name="concurrent:revision-advance",
            scheduler_step_count=waiting.scheduler_step_count + 1,
        )
        store.save(waiting)

    result = scheduler.accept_user_command(
        waiting.execution_id,
        _command(wait, **changes),
    )

    assert result.outcome.disposition in {"REJECT", "STALE"}
    assert result.outcome.reason_code == reason_code
    assert result.state == waiting
    assert store.load(waiting.execution_id) == waiting
    assert commands.calls == []


def test_duplicate_conflict_and_consumed_wait_late_answer_apply_at_most_once():
    scheduler, store, commands, waiting = _waiting_scheduler()
    wait = waiting.current_wait_handle
    command = _command(wait)

    accepted = scheduler.accept_user_command(waiting.execution_id, command)
    persisted = store.load(waiting.execution_id)
    replay = scheduler.accept_user_command(waiting.execution_id, command)
    conflict = scheduler.accept_user_command(
        waiting.execution_id,
        _command(wait, payload={"answer_text": "A different answer."}),
    )
    late = scheduler.accept_user_command(
        waiting.execution_id,
        _command(wait, command_id="late-command"),
    )

    assert accepted.outcome.disposition == "ACCEPT"
    assert replay.outcome.disposition == "REPLAY"
    assert replay.outcome.reason_code == "idempotent_replay"
    assert conflict.outcome.disposition == "CONFLICT"
    assert conflict.outcome.reason_code == "command_payload_conflict"
    assert late.outcome.disposition == "REJECT"
    assert late.outcome.reason_code == "no_active_wait"
    assert replay.state == conflict.state == late.state == persisted
    assert len(commands.calls) == 1
