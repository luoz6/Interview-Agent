from types import SimpleNamespace

from app.services.interview_workflow_consumer import (
    InterviewWorkflowConsumer,
)
from app.services.runtime_domain_events import (
    InterviewCommandReadyEvent,
    InterviewRetryDueEvent,
)


class FakeGraph:
    def __init__(self):
        self.invocations = []
        self.snapshot = SimpleNamespace(
            next=("wait_for_retry",),
            values={
                "generation_id": "gen-1",
                "expected_retry_attempt": 2,
            },
        )

    def get_state(self, config):
        return self.snapshot

    def invoke(self, command, *, config):
        self.invocations.append(command)


class FakeWorkflow:
    def __init__(self):
        self.graph = FakeGraph()

    def graph_for_session(self, session_id):
        return self.graph


def make_consumer():
    workflow = FakeWorkflow()
    consumer = InterviewWorkflowConsumer(workflow)
    consumer.graph = workflow.graph
    return consumer


def test_command_event_resumes_answer_interrupt():
    consumer = make_consumer()

    outcome = consumer.consume(
        InterviewCommandReadyEvent(
            session_id="s1", command_id="cmd-1"
        ).model_dump()
    )

    assert outcome.status == "completed"
    assert consumer.graph.invocations[0].resume["command_id"] == "cmd-1"


def test_retry_event_resumes_timer_interrupt():
    consumer = make_consumer()

    outcome = consumer.consume(
        InterviewRetryDueEvent(
            session_id="s1",
            generation_id="gen-1",
            next_attempt_number=2,
        ).model_dump()
    )

    assert outcome.status == "completed"
    assert consumer.graph.invocations[0].resume["kind"] == "retry_timer"


def test_duplicate_retry_event_is_discarded_before_graph_invoke():
    consumer = make_consumer()
    consumer.graph.snapshot.next = ("wait_for_answer",)

    outcome = consumer.consume(
        InterviewRetryDueEvent(
            session_id="s1",
            generation_id="gen-1",
            next_attempt_number=2,
        ).model_dump()
    )

    assert outcome.status == "discarded_stale_retry"
    assert consumer.graph.invocations == []
