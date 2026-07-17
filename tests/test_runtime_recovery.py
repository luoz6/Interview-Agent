from scripts.runtime_recovery import main


class FakeControl:
    def list_recovery_events(self, *, status, limit):
        return [
            {
                "event_id": "event-1",
                "status": status,
                "attempt_count": 5,
                "replay_count": 0,
                "last_error_code": "provider_unavailable",
            }
        ][:limit]

    def replay_dead_letter(self, event_id):
        if event_id != "event-dead":
            raise ValueError("event is not dead-lettered")
        return {
            "event_id": event_id,
            "status": "pending",
            "replay_count": 1,
            "payload": {"candidate_answer": "secret"},
        }


class FakeJobs:
    def requeue_failed(self, session_id):
        return {
            "job_id": "job-1",
            "session_id": session_id,
            "status": "queued",
            "replay_count": 1,
            "last_error": "secret provider response",
        }


def stores_factory():
    return FakeControl(), FakeJobs()


def test_replay_output_excludes_payload(capsys):
    result = main(
        ["replay-event", "--event-id", "event-dead"],
        stores_factory=stores_factory,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "event-dead" in output
    assert "payload" not in output
    assert "candidate_answer" not in output


def test_invalid_replay_returns_stable_code_only(capsys):
    result = main(
        ["replay-event", "--event-id", "event-pending"],
        stores_factory=stores_factory,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "invalid_recovery_state" in output
    assert "not dead-lettered" not in output


def test_report_requeue_excludes_raw_error(capsys):
    result = main(
        ["requeue-report", "--session-id", "s1"],
        stores_factory=stores_factory,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "secret provider response" not in output
    assert '"last_error":' not in output
