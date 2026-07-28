from dataclasses import dataclass

from app.services.runtime_domain_events import (
    InterviewCommandReadyEvent,
    InterviewRetryDueEvent,
)


@dataclass(frozen=True)
class ConsumerOutcome:
    status: str


class InterviewWorkflowConsumer:
    def __init__(self, workflow) -> None:
        self.workflow = workflow

    def consume(self, payload: dict) -> ConsumerOutcome:
        event_type = payload["event_type"]
        session_id = payload["session_id"]
        if not self.workflow.is_durable_session(session_id):
            return ConsumerOutcome("discarded_wrong_engine")
        if event_type == "interview_command_ready":
            event = InterviewCommandReadyEvent.model_validate(payload)
            return ConsumerOutcome(
                self.workflow.resume_command(session_id, event.command_id)
            )
        elif event_type == "interview_retry_due":
            event = InterviewRetryDueEvent.model_validate(payload)
            config = {"configurable": {"thread_id": session_id}}
            graph = self.workflow.graph_for_session(session_id)
            snapshot = graph.get_state(config)
            state = snapshot.values
            if (
                snapshot.next != ("wait_for_retry",)
                or state.get("generation_id") != event.generation_id
                or state.get("expected_retry_attempt")
                != event.next_attempt_number
            ):
                return ConsumerOutcome("discarded_stale_retry")
            return ConsumerOutcome(
                self.workflow.resume_generation_retry(
                    session_id,
                    generation_id=event.generation_id,
                    next_attempt_number=event.next_attempt_number,
                )
            )
        else:
            raise ValueError("unsupported interview workflow event")
