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
    def get_by_source_command(self, session_id, command_id):
        return type(
            "Generation",
            (),
            {"generation_id": "gen-1"},
        )()

    def list_events(self, generation_id):
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

        return [
            event(1, 1, "chunk", "old"),
            event(1, 2, "chunk", "partial"),
            event(2, 0, "generation_reset"),
            event(2, 1, "chunk", "replacement"),
        ]


def test_reset_event_precedes_replacement_chunks():
    service = InterviewEventStreamService(
        workflow_store=object(),
        generation_store=FakeGenerationStore(),
    )

    events = list(
        service.iter_command_events(
            "s1", "cmd-1", after_event_id="gen-1:1:2"
        )
    )

    assert events[0].event == "generation_reset"
    assert events[0].attempt_number == 2
