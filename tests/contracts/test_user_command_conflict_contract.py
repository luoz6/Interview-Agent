from app.domain.interview.scheduling import (
    UserCommand,
    WaitHandle,
    classify_user_command,
)


def _wait() -> WaitHandle:
    return WaitHandle(
        wait_id="wait-1",
        execution_id="exec-1",
        task_id="task-1",
        question_id="question-1",
        issued_revision=4,
        expected_command_kind="ANSWER",
    )


def _command(**overrides) -> UserCommand:
    values = {
        "command_id": "cmd-1",
        "execution_id": "exec-1",
        "wait_id": "wait-1",
        "task_id": "task-1",
        "question_id": "question-1",
        "expected_revision": 4,
        "payload": {"answer_text": "same"},
        "command_kind": "ANSWER",
    }
    values.update(overrides)
    return UserCommand(**values)


def test_same_command_and_payload_is_idempotent_replay():
    outcome = classify_user_command(
        _command(),
        wait_handle=_wait(),
        current_revision=5,
        recorded_command=_command(),
    )
    assert outcome.disposition == "REPLAY"
    assert outcome.idempotent_replay is True


def test_same_command_with_different_payload_is_conflict():
    outcome = classify_user_command(
        _command(payload={"answer_text": "new"}),
        wait_handle=_wait(),
        current_revision=4,
        recorded_command=_command(payload={"answer_text": "old"}),
    )
    assert outcome.disposition == "CONFLICT"
    assert outcome.reason_code == "command_payload_conflict"


def test_wrong_wait_question_or_old_task_is_rejected():
    wait = _wait()
    for field, value, reason in (
        ("wait_id", "old-wait", "wrong_wait"),
        ("question_id", "old-question", "wrong_question"),
        ("task_id", "old-task", "late_task"),
    ):
        outcome = classify_user_command(
            _command(**{field: value}),
            wait_handle=wait,
            current_revision=4,
        )
        assert outcome.disposition == "REJECT"
        assert outcome.reason_code == reason


def test_stale_revision_is_distinct_from_rejection_and_valid_is_accepted():
    stale = classify_user_command(
        _command(expected_revision=3),
        wait_handle=_wait(),
        current_revision=4,
    )
    assert stale.disposition == "STALE"
    assert stale.reason_code == "stale_revision"

    accepted = classify_user_command(
        _command(),
        wait_handle=_wait(),
        current_revision=4,
    )
    assert accepted.disposition == "ACCEPT"
    assert accepted.accepted is True
