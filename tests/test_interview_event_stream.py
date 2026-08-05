from app.services.interview_event_stream import InterviewEventStreamService
from app.services.runtime_events import InterviewGenerationChunkEvent


def test_chunk_sse_has_replay_cursor():
    event = InterviewGenerationChunkEvent(
        generation_id="gen-1",
        attempt_number=2,
        sequence=3,
        delta="hello",
    )

    assert event.to_sse().startswith(
        "id: gen-1:2:3\nevent: chunk\n"
    )


class FakeGenerationStore:
    def __init__(self):
        self.calls = []

    def get_by_source_command(self, session_id, command_id):
        return type(
            "Generation",
            (),
            {"generation_id": "gen-1"},
        )()

    def list_events_after(
        self,
        generation_id,
        *,
        after_attempt,
        after_sequence,
        limit,
    ):
        self.calls.append(
            (generation_id, after_attempt, after_sequence, limit)
        )

        def event(attempt, sequence, kind, delta=""):
            return type(
                "Event",
                (),
                {
                    "generation_id": generation_id,
                    "attempt_number": attempt,
                    "sequence": sequence,
                    "event_type": kind,
                    "delta": delta,
                },
            )()

        events = [
            event(1, 1, "chunk", "old"),
            event(1, 2, "chunk", "partial"),
            event(2, 0, "generation_reset"),
            event(2, 1, "chunk", "replacement"),
        ]
        return [
            item
            for item in events
            if (item.attempt_number, item.sequence)
            > (after_attempt, after_sequence)
        ][:limit]


def test_reset_event_precedes_replacement_chunks():
    generation_store = FakeGenerationStore()
    service = InterviewEventStreamService(
        workflow_store=object(),
        generation_store=generation_store,
        page_size=1,
    )

    events = list(
        service.iter_command_events(
            "s1", "cmd-1", after_event_id="gen-1:1:2"
        )
    )

    assert events[0].event == "generation_reset"
    assert events[0].attempt_number == 2
    assert generation_store.calls == [
        ("gen-1", 1, 2, 1),
        ("gen-1", 2, 0, 1),
        ("gen-1", 2, 1, 1),
    ]


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class PendingWorkflowStore:
    def get_command(self, session_id, command_id):
        return type("Command", (), {"status": "pending"})()


class AppliedWorkflowStore:
    def get_command(self, session_id, command_id):
        return type(
            "Command",
            (),
            {"status": "applied", "result_state_version": 2},
        )()


def test_sse_announces_generation_pending_before_chunks_without_reasoning():
    service = InterviewEventStreamService(
        workflow_store=AppliedWorkflowStore(),
        generation_store=FakeGenerationStore(),
        page_size=20,
    )

    events = list(service.iter_sse("s1", "cmd-1"))

    assert events[0].startswith("event: status\n")
    assert '"stage": "generation_pending"' in events[0]
    assert "gap" not in events[0]
    assert "confidence" not in events[0]
    assert "reason" not in events[0]
    assert events[1].startswith("id: gen-1:1:1\nevent: chunk\n")
    assert events[-1].startswith("event: done\n")


def test_pending_sse_times_out_with_reconnect_cursor():
    clock = FakeClock()
    service = InterviewEventStreamService(
        workflow_store=PendingWorkflowStore(),
        generation_store=FakeGenerationStore(),
        min_poll_seconds=0.05,
        max_poll_seconds=0.05,
        max_stream_seconds=0.1,
        keepalive_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
    )

    events = list(
        service.iter_sse(
            "s1",
            "cmd-1",
            after_event_id="gen-1:2:1",
        )
    )

    assert events[-1].startswith("event: reconnect\n")
    assert '"last_event_id": "gen-1:2:1"' in events[-1]
