from __future__ import annotations

from app.services.runtime_events import (
    InterviewGenerationChunkEvent,
    InterviewGenerationResetEvent,
)


class InterviewEventStreamService:
    def __init__(self, workflow_store, generation_store) -> None:
        self.workflow_store = workflow_store
        self.generation_store = generation_store

    def iter_command_events(
        self,
        session_id: str,
        command_id: str,
        *,
        after_event_id: str | None = None,
    ):
        generation = self.generation_store.get_by_source_command(
            session_id, command_id
        )
        if generation is None:
            return
        after = self._parse_cursor(after_event_id, generation.generation_id)
        for item in self.generation_store.list_events(
            generation.generation_id
        ):
            cursor = (item.attempt_number, item.sequence)
            if cursor <= after:
                continue
            if item.event_type == "generation_reset":
                yield InterviewGenerationResetEvent(
                    generation_id=item.generation_id,
                    attempt_number=item.attempt_number,
                )
            else:
                yield InterviewGenerationChunkEvent(
                    generation_id=item.generation_id,
                    attempt_number=item.attempt_number,
                    sequence=item.sequence,
                    delta=item.delta,
                )

    def iter_sse(
        self,
        session_id: str,
        command_id: str,
        *,
        after_event_id: str | None = None,
    ):
        for event in self.iter_command_events(
            session_id,
            command_id,
            after_event_id=after_event_id,
        ):
            yield event.to_sse()

    @staticmethod
    def _parse_cursor(
        value: str | None, generation_id: str
    ) -> tuple[int, int]:
        if not value:
            return (0, -1)
        try:
            event_generation, attempt, sequence = value.rsplit(":", 2)
            if event_generation != generation_id:
                return (0, -1)
            return (int(attempt), int(sequence))
        except (TypeError, ValueError):
            return (0, -1)
