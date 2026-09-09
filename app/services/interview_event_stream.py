from __future__ import annotations

from time import monotonic, sleep

from app.services.runtime_events import (
    InterviewGenerationChunkEvent,
    InterviewGenerationResetEvent,
    _format_sse,
    QuestionRevealChunkEvent,
    QuestionRevealDoneEvent,
    QuestionRevealResetEvent,
)


class InterviewEventStreamService:
    def __init__(
        self,
        workflow_store,
        generation_store,
        *,
        page_size: int = 200,
        min_poll_seconds: float = 0.05,
        max_poll_seconds: float = 0.5,
        max_stream_seconds: float = 600,
        keepalive_seconds: float = 15,
        clock=monotonic,
        sleeper=sleep,
    ) -> None:
        if page_size < 1:
            raise ValueError("SSE event page size must be positive")
        if min_poll_seconds <= 0 or max_poll_seconds < min_poll_seconds:
            raise ValueError("invalid SSE poll interval")
        if max_stream_seconds <= 0 or keepalive_seconds <= 0:
            raise ValueError("SSE timeout bounds must be positive")
        self.workflow_store = workflow_store
        self.generation_store = generation_store
        self.page_size = page_size
        self.min_poll_seconds = min_poll_seconds
        self.max_poll_seconds = max_poll_seconds
        self.max_stream_seconds = max_stream_seconds
        self.keepalive_seconds = keepalive_seconds
        self._clock = clock
        self._sleep = sleeper

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
        while True:
            page = self.generation_store.list_events_after(
                generation.generation_id,
                after_attempt=after[0],
                after_sequence=after[1],
                limit=self.page_size,
            )
            for item in page:
                after = (item.attempt_number, item.sequence)
                main_question = (
                    getattr(generation, "generation_kind", "followup")
                    == "main_question"
                )
                if item.event_type == "generation_reset":
                    if main_question:
                        yield QuestionRevealResetEvent(
                            question_id=generation.question_id,
                            generation_id=item.generation_id,
                            attempt_number=item.attempt_number,
                        )
                    else:
                        yield InterviewGenerationResetEvent(
                            generation_id=item.generation_id,
                            attempt_number=item.attempt_number,
                        )
                else:
                    if main_question:
                        yield QuestionRevealChunkEvent(
                            question_id=generation.question_id,
                            generation_id=item.generation_id,
                            sequence=item.sequence,
                            delta=item.delta,
                            attempt_number=item.attempt_number,
                        )
                    else:
                        yield InterviewGenerationChunkEvent(
                            generation_id=item.generation_id,
                            attempt_number=item.attempt_number,
                            sequence=item.sequence,
                            delta=item.delta,
                        )
            if len(page) < self.page_size:
                return

    def iter_sse(
        self,
        session_id: str,
        command_id: str,
        *,
        after_event_id: str | None = None,
    ):
        cursor = after_event_id
        started_at = self._clock()
        last_keepalive_at = started_at
        poll_seconds = self.min_poll_seconds
        announced_generation_id = None
        while True:
            now = self._clock()
            if now - started_at >= self.max_stream_seconds:
                yield _format_sse(
                    "reconnect",
                    {
                        "command_id": command_id,
                        "last_event_id": cursor,
                        "retry_after_ms": round(poll_seconds * 1000),
                    },
                )
                return
            emitted = False
            generation = self.generation_store.get_by_source_command(
                session_id, command_id
            )
            command = self.workflow_store.get_command(
                session_id, command_id
            )
            main_question = (
                generation is not None
                and getattr(generation, "generation_kind", "followup")
                == "main_question"
            )
            if (
                generation is not None
                and generation.generation_id != announced_generation_id
            ):
                announced_generation_id = generation.generation_id
                yield _format_sse(
                    "status",
                    {
                        "stage": (
                            "main_question_generation"
                            if getattr(
                                generation, "generation_kind", "followup"
                            ) == "main_question"
                            else "generation_pending"
                        ),
                        **(
                            {"question_id": generation.question_id}
                            if getattr(
                                generation, "generation_kind", "followup"
                            ) == "main_question"
                            else {}
                        ),
                        "generation_id": generation.generation_id,
                    },
                )
                emitted = True
            # Main-question chunks are committed-text reveal events. Do not
            # expose the completed Generation until Graph projection has
            # atomically published RenderedQuestion and applied the command.
            if not main_question or command.status == "applied":
                if main_question and not cursor:
                    reset = QuestionRevealResetEvent(
                        question_id=generation.question_id,
                        generation_id=generation.generation_id,
                        attempt_number=int(generation.active_attempt or 0),
                    )
                    cursor = (
                        f"{generation.generation_id}:"
                        f"{int(generation.active_attempt or 0)}:0"
                    )
                    yield reset.to_sse()
                    emitted = True
                for event in self.iter_command_events(
                    session_id,
                    command_id,
                    after_event_id=cursor,
                ):
                    cursor = (
                        f"{event.generation_id}:{event.attempt_number}:"
                        f"{getattr(event, 'sequence', 0)}"
                    )
                    yield event.to_sse()
                    emitted = True
            if command.status == "applied":
                if (
                    generation is not None
                    and getattr(generation, "generation_kind", "followup")
                    == "main_question"
                ):
                    yield QuestionRevealDoneEvent(
                        question_id=generation.question_id,
                        generation_id=generation.generation_id,
                        state_version=command.result_state_version,
                    ).to_sse()
                else:
                    yield _format_sse(
                        "done",
                        {
                            "command_id": command_id,
                            "state_version": command.result_state_version,
                        },
                    )
                return
            if command.status == "conflict":
                yield _format_sse(
                    "conflict",
                    {"code": command.error_code or "state_version_conflict"},
                )
                return
            if command.status == "failed":
                yield _format_sse(
                    "error",
                    {"code": command.error_code or "workflow_failed"},
                )
                return
            now = self._clock()
            if now - last_keepalive_at >= self.keepalive_seconds:
                yield ": keepalive\n\n"
                last_keepalive_at = now
            if emitted:
                poll_seconds = self.min_poll_seconds
            else:
                poll_seconds = min(
                    self.max_poll_seconds,
                    poll_seconds * 2,
                )
            self._sleep(poll_seconds)

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
