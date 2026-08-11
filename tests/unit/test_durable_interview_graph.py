from dataclasses import dataclass
from threading import Event

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    GenerationLeaseHeartbeat,
    build_durable_interview_graph,
    generate_followup,
    project_state_node,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.workflow_thread_lock import GenerationLeaseLost
from tests.unit.test_durable_interview_state import make_start_kwargs


def test_v2_projection_clears_only_bounded_active_artifact_reference_fields():
    projection = type("Projection", (), {"state_version": 4})()
    deps = type(
        "Deps",
        (),
        {
            "project_state": None,
            "workflow_store": type(
                "Store", (), {"project_state": lambda self, state: projection}
            )(),
        },
    )()
    state = {
        "workflow_engine": "langgraph-v2",
        "command_outcome": None,
        "active_context_artifact_ref": "context-artifact-ref:abc",
    }

    result = project_state_node(state, deps)

    assert result["active_context_artifact_ref"] is None
    assert result["active_context_artifact_sha256"] is None
    assert result["active_context_artifact_type"] is None
    assert result["active_context_policy_version"] is None
    assert result["context_route"] is None


@dataclass
class FakeCommand:
    command_id: str
    status: str
    expected_version: int = 1
    command_type: str = "answer"
    answer_text: str | None = "answer"


class FakeWorkflowStore:
    def __init__(self):
        self.commands = {}
        self.loaded_commands = []
        self.marked_conflicts = []

    def seed_command(self, command_id, *, status):
        self.commands[command_id] = FakeCommand(command_id, status)

    def get_command(self, session_id, command_id):
        self.loaded_commands.append((session_id, command_id))
        return self.commands[command_id]

    def mark_command_conflict(self, session_id, command_id, state_version):
        self.marked_conflicts.append(
            (session_id, command_id, state_version)
        )
        self.commands[command_id].status = "conflict"


def make_graph():
    store = FakeWorkflowStore()

    def project_state(state):
        return {
            "state_version": state["state_version"] + 1,
            "command_outcome": None,
        }

    deps = DurableInterviewGraphDependencies(store, project_state)
    graph = build_durable_interview_graph(
        deps, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "s1"}}
    return graph, config, deps


def make_initial_input():
    kwargs = make_start_kwargs()
    return make_durable_initial_state("s1", kwargs["plan"])


def test_graph_initializes_then_waits_for_answer():
    graph, config, _ = make_graph()

    result = graph.invoke(make_initial_input(), config=config)

    assert result["interview_status"] == "active"
    snapshot = graph.get_state(config)
    assert snapshot.next == ("wait_for_answer",)
    assert snapshot.tasks[0].interrupts


def test_answer_resume_stores_only_command_identity():
    graph, config, deps = make_graph()
    deps.workflow_store.seed_command("cmd-1", status="applied")
    graph.invoke(make_initial_input(), config=config)

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    assert deps.workflow_store.loaded_commands == [("s1", "cmd-1")]
    assert graph.get_state(config).next == ("wait_for_answer",)


def test_conflicted_command_replay_is_idempotent():
    graph, config, deps = make_graph()
    deps.workflow_store.seed_command("cmd-conflict", status="conflict")
    graph.invoke(make_initial_input(), config=config)

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-conflict",
            }
        ),
        config=config,
    )

    assert graph.get_state(config).next == ("wait_for_answer",)
    assert deps.workflow_store.marked_conflicts == []


def test_generation_heartbeat_is_throttled_independently_of_chunk_flushes():
    class AlwaysFlushCoalescer:
        def add(self, value):
            return value

        def flush(self):
            return None

    class GenerationStore:
        def __init__(self):
            self.heartbeats = 0

        def start_or_reclaim_attempt(self, *args, **kwargs):
            return type(
                "Attempt",
                (),
                {
                    "generation_id": "gen-1",
                    "attempt_number": 1,
                    "lease_token": "token-1",
                    "fencing_version": 1,
                },
            )()

        def append_chunk(self, *args, **kwargs):
            pass

        def heartbeat_attempt(self, *args, **kwargs):
            self.heartbeats += 1
            return True

        def complete_attempt(self, *args, **kwargs):
            pass

    class Heartbeat:
        def __init__(self, *, generation_store, attempt, **kwargs):
            self.generation_store = generation_store
            self.attempt = attempt

        def __enter__(self):
            self.generation_store.heartbeat_attempt(
                self.attempt.generation_id,
                self.attempt.attempt_number,
                "worker",
                lease_token=self.attempt.lease_token,
                fencing_version=self.attempt.fencing_version,
            )
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            return None

    class Examiner:
        def stream_followup_attempt(self, **kwargs):
            yield from ("a", "b", "c", "d")

    state = make_initial_input()
    state.update(
        {
            "active_command_id": "cmd-1",
            "generation_id": "gen-1",
            "generation_attempt": 1,
            "state_version": 1,
        }
    )
    generation_store = GenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=generation_store,
        examiner=Examiner(),
        coalescer_factory=AlwaysFlushCoalescer,
        generation_lease_seconds=3,
        generation_heartbeat_factory=Heartbeat,
    )

    result = generate_followup(state, deps)

    assert result["generated_text"] == "abcd"
    assert generation_store.heartbeats == 1


def test_generation_heartbeat_exception_fails_closed_with_original_cause():
    failure = RuntimeError("renewal unavailable")

    class RaisingStore:
        def __init__(self):
            self.called = Event()

        def heartbeat_attempt(self, *args, **kwargs):
            self.called.set()
            raise failure

    attempt = type(
        "Attempt",
        (),
        {
            "generation_id": "gen-1",
            "attempt_number": 1,
            "lease_token": "token-1",
            "fencing_version": 1,
        },
    )()
    store = RaisingStore()
    heartbeat = GenerationLeaseHeartbeat(
        generation_store=store,
        attempt=attempt,
        worker_id="worker-1",
        lease_seconds=30,
    )
    heartbeat.interval_seconds = 0.01

    with heartbeat:
        assert store.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(GenerationLeaseLost) as caught:
            heartbeat.ensure_owned()

    assert caught.value.__cause__ is failure
    assert heartbeat._thread is not None
    assert not heartbeat._thread.is_alive()


def test_generation_heartbeat_preserves_first_failure():
    attempt = type(
        "Attempt",
        (),
        {
            "generation_id": "gen-1",
            "attempt_number": 1,
            "lease_token": "token-1",
            "fencing_version": 1,
        },
    )()
    heartbeat = GenerationLeaseHeartbeat(
        generation_store=object(),
        attempt=attempt,
        worker_id="worker-1",
        lease_seconds=30,
    )
    first = RuntimeError("first")
    second = RuntimeError("second")

    heartbeat._mark_lost(first)
    heartbeat._mark_lost(second)

    with pytest.raises(GenerationLeaseLost) as caught:
        heartbeat.ensure_owned()
    assert caught.value.__cause__ is first


def test_generation_lease_loss_stops_before_any_stale_mutation():
    class ImmediateCoalescer:
        def add(self, value):
            return value

        def flush(self):
            return None

    class GenerationStore:
        def __init__(self):
            self.appended = []
            self.completed = []
            self.failed = []

        def start_or_reclaim_attempt(self, *args, **kwargs):
            return type(
                "Attempt",
                (),
                {
                    "generation_id": "gen-1",
                    "attempt_number": 1,
                    "lease_token": "token-1",
                    "fencing_version": 1,
                },
            )()

        def append_chunk(self, *args, **kwargs):
            self.appended.append(args)

        def complete_attempt(self, *args, **kwargs):
            self.completed.append(args)

        def fail_attempt(self, *args, **kwargs):
            self.failed.append(args)

    class LostHeartbeat:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            raise GenerationLeaseLost("renewal unavailable")

    class Examiner:
        def stream_followup_attempt(self, **kwargs):
            yield "generated"

    state = make_initial_input()
    state.update(
        {
            "active_command_id": "cmd-1",
            "generation_id": "gen-1",
            "generation_attempt": 1,
            "state_version": 1,
        }
    )
    store = GenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        generation_store=store,
        examiner=Examiner(),
        coalescer_factory=ImmediateCoalescer,
        generation_heartbeat_factory=LostHeartbeat,
    )

    with pytest.raises(GenerationLeaseLost):
        generate_followup(state, deps)

    assert store.appended == []
    assert store.completed == []
    assert store.failed == []
