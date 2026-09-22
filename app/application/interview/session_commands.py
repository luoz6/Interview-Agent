from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
import logging
from typing import Any, Literal

from app.domain.interview.commands import SessionCommand
from app.domain.interview.errors import SessionDeletingError
from app.domain.interview.models import InterviewTurn
from app.domain.interview.state_machine import extract_follow_up, turn_from_state
from app.domain.interview.state import is_durable_interview_version
from app.ports.runtime import (
    InterviewSessionRepository,
    ReportJobQueue,
    RuntimeEventPublisher,
)
from app.domain.interview.rounds import round_closed_event_from_transition
from app.domain.agent_execution import correlation_id_from_plan
from app.domain.runtime_events import RoundClosedEvent
from app.domain.interview.prep import public_interview_plan_payload
from app.application.report.enqueue import enqueue_report_if_needed
from app.application.interview.events import (
    AcceptedInterviewCommand,
    InterviewStreamChunkEvent,
    InterviewStreamDoneEvent,
    InterviewStreamErrorEvent,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionCommandResult:
    kind: Literal["legacy", "durable"]
    turn: InterviewTurn | None = None
    accepted: AcceptedInterviewCommand | None = None


@dataclass(frozen=True)
class DurableSessionStream:
    events: Iterator[str]
    owner: Any | None = None


@dataclass(frozen=True)
class LegacySessionStream:
    events: Iterator[
        InterviewStreamChunkEvent
        | InterviewStreamDoneEvent
        | InterviewStreamErrorEvent
    ]


class SessionCommandService:
    """Execute session commands without HTTP or concrete-store knowledge."""

    def __init__(
        self,
        *,
        store: InterviewSessionRepository,
        workflow_service_factory: Callable[[], Any],
        publisher: RuntimeEventPublisher,
        report_job_store_factory: Callable[[], ReportJobQueue],
        execution_path_router: Any | None = None,
        scheduler_entry_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self.workflow_service_factory = workflow_service_factory
        self.publisher = publisher
        self.report_job_store_factory = report_job_store_factory
        self.execution_path_router = execution_path_router
        self.scheduler_entry_factory = scheduler_entry_factory

    def workflow_service(self):
        return self.workflow_service_factory()

    def require_active_state(self, session_id: str):
        state = self.store.get(session_id)
        if state.get("deletion_status") == "deleting":
            raise SessionDeletingError(session_id)
        return state

    def execute(self, command: SessionCommand) -> SessionCommandResult:
        state = self.require_active_state(command.session_id)
        if self._is_scheduler_execution(command.session_id):
            entry = self.scheduler_entry_factory()
            before = entry.snapshot(command.session_id)
            turn = entry.execute(command)
            after = entry.snapshot(command.session_id)
            self._publish_scheduler_round_closed(command, before=before, after=after)
            return SessionCommandResult(kind="legacy", turn=turn)
        if is_durable_interview_version(state.get("workflow_engine")):
            accepted = self.workflow_service().submit_command(
                command.session_id,
                command_type=command.command_type,
                expected_version=command.expected_version,
                command_id=command.command_id,
                answer_text=command.answer_text,
            )
            return SessionCommandResult(kind="durable", accepted=accepted)

        before_state = deepcopy(state)
        kwargs = {
            "expected_version": command.expected_version,
            "command_id": command.command_id,
        }
        if command.command_type == "answer":
            turn = self.store.submit_answer(
                command.session_id,
                command.answer_text or "",
                **kwargs,
            )
        else:
            turn = getattr(self.store, command.command_type)(
                command.session_id,
                **kwargs,
            )
        after_state = deepcopy(self.store.get(command.session_id))
        self.complete_legacy_transition(
            command.session_id,
            before_state=before_state,
            after_state=after_state,
            turn=turn,
        )
        return SessionCommandResult(kind="legacy", turn=turn)

    def _publish_scheduler_round_closed(self, command, *, before, after) -> None:
        if getattr(self.store, "runtime_event_delivery", None) == "transactional_outbox":
            return
        before_question = before.get("current_question") or {}
        after_question = after.get("current_question") or {}
        question_id = before_question.get("id")
        if not question_id:
            return
        if (
            after.get("status") != "finished"
            and after_question.get("id") == question_id
        ):
            return
        source = self.store.get(command.session_id)
        try:
            self.publisher.publish(
                RoundClosedEvent(
                    session_id=command.session_id,
                    correlation_id=correlation_id_from_plan(
                        source["plan"],
                        session_id=command.session_id,
                    ),
                    causation_id=command.command_id or after.get("last_command_id"),
                    state_version=after.get("state_version"),
                    question_id=question_id,
                    answer_state=(
                        "skipped" if command.command_type == "skip" else "answered"
                    ),
                    job_tags=list(after.get("job_tags") or source.get("job_tags") or []),
                )
            )
        except Exception:
            logger.exception(
                "round_closed event publish failed",
                extra={"session_id": command.session_id},
            )

    def _is_scheduler_execution(self, session_id: str) -> bool:
        if self.execution_path_router is None or self.scheduler_entry_factory is None:
            return False
        binding = self.execution_path_router.binding_store.get(session_id)
        return binding is not None and binding.path == "NEW"

    def complete_legacy_transition(
        self,
        session_id: str,
        *,
        before_state,
        after_state,
        turn: InterviewTurn,
    ) -> None:
        publish_round_closed_event(
            self.publisher,
            self.store,
            before_state,
            after_state,
        )
        enqueue_report_if_needed(
            turn_status=turn.status,
            session_id=session_id,
            store=self.store,
            job_store_factory=self.report_job_store_factory,
        )


class InterviewApplicationService(SessionCommandService):
    """Application facade for session commands and public snapshots."""

    def snapshot(self, session_id: str) -> dict[str, Any]:
        state = self.require_active_state(session_id)
        if self._is_scheduler_execution(session_id):
            snapshot = self.scheduler_entry_factory().snapshot(session_id)
        else:
            snapshot = (
                self.workflow_service().snapshot(session_id)
                if is_durable_interview_version(state.get("workflow_engine"))
                else self.store.snapshot(session_id)
            )
        public_plan = public_interview_plan_payload(state["plan"])
        snapshot["prep_context"] = public_plan.get("prep_context")
        return snapshot


class StreamingTurnService:
    def __init__(
        self,
        application: InterviewApplicationService,
        *,
        scheduler_stream_factory: Callable[[Any, SessionCommand], Any] | None = None,
    ) -> None:
        self.application = application
        self.scheduler_stream_factory = scheduler_stream_factory

    def prepare(
        self,
        command: SessionCommand,
    ) -> DurableSessionStream | LegacySessionStream:
        state = self.application.require_active_state(command.session_id)
        scheduler_entry = None
        if self.application._is_scheduler_execution(command.session_id):
            scheduler_entry = self.application.scheduler_entry_factory()
            if not command.command_id:
                command = replace(
                    command,
                    command_id=scheduler_entry.id_generator(),
                )
            if self.scheduler_stream_factory is not None:
                before = scheduler_entry.snapshot(command.session_id)
                stream = self.scheduler_stream_factory(scheduler_entry, command)

                def scheduler_stream_events():
                    for event in stream.events:
                        yield event
                    after = scheduler_entry.snapshot(command.session_id)
                    self.application._publish_scheduler_round_closed(
                        command,
                        before=before,
                        after=after,
                    )

                return DurableSessionStream(
                    events=scheduler_stream_events(),
                    owner=stream.owner,
                )
            turn = scheduler_entry.execute(command)

            def scheduler_events():
                yield InterviewStreamDoneEvent(turn=turn_to_dict(turn))

            return LegacySessionStream(events=scheduler_events())
        if scheduler_entry is None and is_durable_interview_version(
            state.get("workflow_engine")
        ):
            workflow = self.application.workflow_service()
            accepted = workflow.submit_command(
                command.session_id,
                command_type="answer",
                expected_version=command.expected_version,
                command_id=command.command_id,
                answer_text=command.answer_text,
            )
            return DurableSessionStream(
                events=workflow.event_stream.iter_sse(
                    command.session_id,
                    accepted.command_id,
                )
            )

        before_state = deepcopy(state)
        prepared = self.application.store.prepare_streaming_answer(
            command.session_id,
            command.answer_text or "",
            expected_version=command.expected_version,
            command_id=command.command_id,
        )

        def iter_events():
            try:
                if prepared.stream_follow_up:
                    chunks: list[str] = []
                    for chunk in self.application.store.stream_followup(
                        command.session_id
                    ):
                        chunks.append(chunk)
                        yield InterviewStreamChunkEvent(delta=chunk)
                    follow_up_text = "".join(chunks).strip()
                else:
                    decision = prepared.state["decision"]
                    follow_up_text = (
                        decision.get("follow_up") if decision else None
                    )

                finalized_state = (
                    self.application.store.complete_streaming_answer(
                        command.session_id,
                        follow_up_text=follow_up_text,
                        expected_version=prepared.state["state_version"],
                        command_id=command.command_id,
                    )
                )
                after_state = deepcopy(finalized_state)
                turn = turn_from_state(
                    finalized_state,
                    follow_up=extract_follow_up(finalized_state),
                )
                self.application.complete_legacy_transition(
                    command.session_id,
                    before_state=before_state,
                    after_state=after_state,
                    turn=turn,
                )
                yield InterviewStreamDoneEvent(turn=turn_to_dict(turn))
            except Exception as exc:  # pragma: no cover - stream boundary
                yield InterviewStreamErrorEvent(detail=str(exc))

        return LegacySessionStream(events=iter_events())


def publish_round_closed_event(
    publisher: RuntimeEventPublisher,
    store: InterviewSessionRepository,
    before_state,
    after_state,
) -> None:
    if getattr(store, "runtime_event_delivery", "direct") == "transactional_outbox":
        return
    event = round_closed_event_from_transition(before_state, after_state)
    if event is None:
        return
    try:
        publisher.publish(event)
    except Exception as exc:
        logger.warning(
            "round_closed event publish failed",
            extra={
                "session_id": event.session_id,
                "question_id": event.question_id,
            },
            exc_info=exc,
        )


def turn_to_dict(turn: InterviewTurn) -> dict[str, Any]:
    return {
        "session_id": turn.session_id,
        "current_question": (
            turn.current_question.model_dump()
            if turn.current_question
            else None
        ),
        "follow_up": turn.follow_up,
        "status": turn.status,
    }


__all__ = [
    "DurableSessionStream",
    "InterviewApplicationService",
    "LegacySessionStream",
    "SessionCommandService",
    "SessionCommandResult",
    "StreamingTurnService",
    "publish_round_closed_event",
    "turn_to_dict",
]
