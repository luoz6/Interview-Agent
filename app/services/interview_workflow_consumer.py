from dataclasses import dataclass

from langgraph.types import Command

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
        config = {
            "configurable": {"thread_id": payload["session_id"]}
        }
        graph = self.workflow.graph_for_session(payload["session_id"])
        if event_type == "interview_command_ready":
            event = InterviewCommandReadyEvent.model_validate(payload)
            resume = {
                "kind": "answer_command",
                "command_id": event.command_id,
            }
        elif event_type == "interview_retry_due":
            event = InterviewRetryDueEvent.model_validate(payload)
            snapshot = graph.get_state(config)
            state = snapshot.values
            if (
                snapshot.next != ("wait_for_retry",)
                or state.get("generation_id") != event.generation_id
                or state.get("expected_retry_attempt")
                != event.next_attempt_number
            ):
                return ConsumerOutcome("discarded_stale_retry")
            resume = {
                "kind": "retry_timer",
                "generation_id": event.generation_id,
                "next_attempt_number": event.next_attempt_number,
            }
        else:
            raise ValueError("unsupported interview workflow event")
        graph.invoke(Command(resume=resume), config=config)
        return ConsumerOutcome("completed")
