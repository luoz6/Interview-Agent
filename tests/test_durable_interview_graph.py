from dataclasses import dataclass
import re
from threading import Event
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import psycopg2
from psycopg2 import sql

from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    GenerationLeaseHeartbeat,
    build_durable_interview_graph,
    execute_decision_attempt,
    generate_followup,
    prepare_or_load_decision,
    project_state_node,
    _is_duplicate_followup_text,
    _followup_guard_updates,
    MAX_FOLLOWUP_NODE_STEPS_PER_COMMAND,
    MAX_FOLLOWUP_PROVIDER_INVOCATIONS_PER_COMMAND,
    MAX_FOLLOWUP_GENERATION_ENTRIES_PER_COMMAND,
    MAX_FOLLOWUP_STREAM_EVENTS_PER_COMMAND,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.interview_generation_store import (
    PostgresInterviewGenerationStore,
)
from app.services.decision_store import InMemoryDecisionStore
from app.services.followup_decision_service import (
    FollowupDecisionExecutionService,
)
from app.services.postgres_decision_store import PostgresDecisionStore
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from app.services.report import ReportGenerationFailed
from app.services.workflow_thread_lock import GenerationLeaseLost
from tests.test_durable_interview_state import make_start_kwargs
from tests.test_postgres_session_store import require_dsn


DURABLE_GRAPH_TEST_TABLE = re.compile(
    r"^test_durable_graph_[0-9a-f]{10}_[a-z0-9_]+$"
)


def _durable_graph_test_tables() -> set[str]:
    with psycopg2.connect(require_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename LIKE 'test_durable_graph_%'
                """
            )
            return {row[0] for row in cursor.fetchall()}


@pytest.fixture
def durable_graph_table_cleanup():
    before = _durable_graph_test_tables()
    yield
    created = _durable_graph_test_tables() - before
    if not created:
        return
    if any(DURABLE_GRAPH_TEST_TABLE.fullmatch(name) is None for name in created):
        pytest.fail("refusing to clean a non-isolated durable graph relation")
    with psycopg2.connect(require_dsn()) as connection:
        with connection.cursor() as cursor:
            for name in sorted(created):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(name)
                    )
                )
    assert _durable_graph_test_tables() == before


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


def test_adaptive_graph_routes_only_from_persisted_decision_and_replays_after_crash():
    provider_calls = []

    def provider(context):
        provider_calls.append(context)
        return {
            "action": "next_question",
            "answer_state": "complete",
            "gap_type": "none",
            "gap_summary": "",
            "reason_code": "answer_complete",
            "decision_confidence": "high",
            "closed_gap_ids": [],
            "policy_version": "adaptive_v1",
        }

    inner = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=provider,
    )

    class CrashAfterDecision:
        def __init__(self, target):
            self.target = target
            self.crashed = False

        @property
        def store(self):
            return self.target.store

        def execute(self, *args, **kwargs):
            result = self.target.execute(*args, **kwargs)
            if not self.crashed:
                self.crashed = True
                raise RuntimeError("lost after durable decision completion")
            return result

    graph, config, deps = make_graph()
    deps.decision_service = CrashAfterDecision(inner)
    deps.workflow_store.seed_command("cmd-adaptive", status="pending")
    initial = make_initial_input()
    initial["configuration_snapshot"] = {"followup_policy_version": "adaptive_v1"}
    initial["followup_policy_version"] = "adaptive_v1"
    graph.invoke(initial, config=config)

    with pytest.raises(RuntimeError, match="durable decision completion"):
        graph.invoke(
            Command(
                resume={
                    "kind": "answer_command",
                    "command_id": "cmd-adaptive",
                }
            ),
            config=config,
        )

    assert len(provider_calls) == 1
    interrupted = graph.get_state(config)
    assert interrupted.next == ("execute_decision_attempt",)
    decision_id = interrupted.values["active_decision_id"]
    stored = inner.store.get(decision_id)
    assert stored.final_decision.action == "next_question"
    assert stored.final_decision.reason_code == "answer_complete"

    # Replace only the in-process wrapper; the same durable store remains.
    deps.decision_service = inner
    resumed = graph.invoke(None, config=config)

    assert resumed["current_index"] == 1
    assert len(provider_calls) == 1


def test_graph_derived_two_followup_limit_makes_zero_decision_provider_calls():
    state = make_initial_input()
    question = state["plan_snapshot"]["questions"][0]
    state.update(
        {
            "active_command_id": "cmd-limit",
            "configuration_snapshot": {
                "followup_policy_version": "adaptive_v1"
            },
            "followup_policy_version": "adaptive_v1",
            "messages": [
                {
                    "role": "interviewer",
                    "content": question["prompt"],
                    "question_id": question["id"],
                },
                {
                    "role": "candidate",
                    "content": "first answer",
                    "question_id": question["id"],
                },
                {
                    "role": "interviewer",
                    "content": "first follow-up",
                    "question_id": question["id"],
                },
                {
                    "role": "candidate",
                    "content": "second answer",
                    "question_id": question["id"],
                },
                {
                    "role": "interviewer",
                    "content": "second follow-up",
                    "question_id": question["id"],
                },
                {
                    "role": "candidate",
                    "content": "third answer",
                    "question_id": question["id"],
                },
            ],
        }
    )
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: (_ for _ in ()).throw(
            AssertionError("Provider must not run at the graph follow-up limit")
        ),
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=FakeWorkflowStore(),
        decision_service=service,
    )

    state.update(prepare_or_load_decision(state, deps))
    state.update(execute_decision_attempt(state, deps))

    assert state["current_followup_count"] == 2
    assert state["decision_action"] == "next_question"
    assert state["decision_reason_code"] == "followup_limit_reached"
    assert service.store.list_attempts(state["active_decision_id"])[
        0
    ].provider_invocations == 0


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
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one key implementation detail.",
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


def test_near_duplicate_followup_is_detected_without_rejecting_new_question():
    state = make_initial_input()
    state["messages"] = [
        state["messages"][0],
        {
            "role": "candidate",
            "content": "I persist the write before acknowledging.",
            "question_id": "q1",
        },
        {
            "role": "interviewer",
            "content": "请具体说明失败写入后的恢复步骤和幂等保障。",
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "content": "I replay from the durable log.",
            "question_id": "q1",
        },
    ]

    assert _is_duplicate_followup_text(
        state, "请具体说明失败写入后的恢复步骤与幂等保障。"
    )
    assert not _is_duplicate_followup_text(
        state, "当恢复日志本身损坏时，你会如何验证并回滚？"
    )


@pytest.mark.parametrize(
    ("updates", "kwargs", "expected_reason"),
    [
        (
            {"command_node_steps": MAX_FOLLOWUP_NODE_STEPS_PER_COMMAND},
            {"action": "decision", "step_increment": 1},
            "node_step_limit_reached",
        ),
        (
            {
                "command_provider_invocations": (
                    MAX_FOLLOWUP_PROVIDER_INVOCATIONS_PER_COMMAND
                )
            },
            {
                "action": "generation",
                "step_increment": 1,
                "provider_call_expected": True,
            },
            "provider_call_limit_reached",
        ),
        (
            {
                "command_generation_entries": (
                    MAX_FOLLOWUP_GENERATION_ENTRIES_PER_COMMAND
                ),
                "command_generation_followup_count": 0,
            },
            {
                "action": "generation",
                "step_increment": 1,
                "generation_entry": True,
            },
            "followup_progress_stalled",
        ),
        (
            {"command_last_checkpoint_version": 0, "state_version": 0},
            {
                "action": "generation",
                "step_increment": 1,
                "checkpoint_observed": True,
            },
            "checkpoint_stalled",
        ),
    ],
)
def test_followup_guard_has_stable_fail_closed_reasons(
    updates, kwargs, expected_reason
):
    state = make_initial_input()
    state.update(updates)

    result = _followup_guard_updates(state, **kwargs)

    assert result["followup_guard_reason_code"] == expected_reason


def test_followup_guard_detects_same_state_and_action_repetition():
    state = make_initial_input()
    first = _followup_guard_updates(
        state, action="decision", step_increment=1
    )
    state.update(first)

    repeated = _followup_guard_updates(
        state, action="decision", step_increment=1
    )

    assert repeated["followup_guard_reason_code"] == "repeated_state"


def test_stream_event_limit_fails_attempt_closed_with_diagnostic_code():
    class Store:
        def __init__(self):
            self.failed = []

        def start_or_reclaim_attempt(self, *args, **kwargs):
            return type(
                "Attempt",
                (),
                {
                    "generation_id": "gen-event-limit",
                    "attempt_number": 1,
                    "lease_token": "lease",
                    "fencing_version": 1,
                },
            )()

        def append_chunk(self, *args, **kwargs):
            pass

        def complete_attempt(self, *args, **kwargs):
            pytest.fail("event-limited generation must not complete")

        def fail_attempt(self, *args, **kwargs):
            self.failed.append((args, kwargs))

    class Heartbeat:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            pass

    class Examiner:
        def stream_followup_attempt(self, **kwargs):
            for _ in range(MAX_FOLLOWUP_STREAM_EVENTS_PER_COMMAND + 1):
                yield "x"

    state = make_initial_input()
    state.update(
        {
            "active_command_id": "cmd-event-limit",
            "generation_id": "gen-event-limit",
            "generation_attempt": 1,
            "state_version": 1,
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one implementation detail.",
        }
    )
    store = Store()
    result = generate_followup(
        state,
        DurableInterviewGraphDependencies(
            workflow_store=FakeWorkflowStore(),
            generation_store=store,
            examiner=Examiner(),
            generation_heartbeat_factory=Heartbeat,
        ),
    )

    assert result["generation_outcome"] == "terminal"
    assert result["last_error_code"] == "event_limit_reached"
    assert result["command_provider_invocations"] == 1
    assert store.failed[0][0][2] == "event_limit_reached"


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
            "decision_gap_type": "clarification",
            "decision_gap_summary": "Clarify one key implementation detail.",
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


class FakeExaminer:
    def __init__(self, *, fail=False, output="Generated follow-up."):
        self.fail = fail
        self.output = output
        self.attempt_count = 0

    def stream_followup_attempt(self, *, context, execution_context):
        self.attempt_count += 1
        if self.fail:
            raise ReportGenerationFailed("provider unavailable")
        yield self.output


def make_postgres_graph(*, fail=False, output="Generated follow-up."):
    prefix = f"test_durable_graph_{uuid4().hex[:10]}"
    session_id = f"session-{uuid4().hex}"
    plan = make_start_kwargs()["plan"].model_copy(
        update={
            "questions": [
                *make_start_kwargs()["plan"].questions,
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                ),
            ]
        }
    )
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    session_store.insert_durable_session_shell(
        session_id=session_id,
        plan=plan,
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    decision_store = PostgresDecisionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    examiner = FakeExaminer(fail=fail, output=output)
    report_jobs = PostgresReportJobStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    workflow_store.report_jobs = report_jobs
    workflow_store.generation_store = generation_store
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        decision_service=FollowupDecisionExecutionService(
            store=decision_store,
            provider=None,
        ),
        examiner=examiner,
        report_job_queue=report_jobs,
    )
    graph = build_durable_interview_graph(
        deps, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": session_id}}
    graph.invoke(
        make_durable_initial_state(session_id, plan), config=config
    )
    return graph, config, workflow_store, examiner


@pytest.mark.pg_runtime
def test_successful_generation_commits_one_complete_message(
    durable_graph_table_cleanup,
):
    graph, config, store, examiner = make_postgres_graph()
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    state = graph.get_state(config).values
    assert state["messages"][-1]["content"] == "Generated follow-up."
    assert state["generation_id"] is None
    assert state["state_version"] == 3
    assert examiner.attempt_count == 1
    assert store.get_command(session_id, "cmd-1").status == "applied"


@pytest.mark.pg_runtime
def test_duplicate_main_question_is_not_committed_and_replay_does_not_regenerate(
    durable_graph_table_cleanup,
):
    graph, config, store, examiner = make_postgres_graph(
        output="Explain an API boundary."
    )
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-duplicate-question",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-duplicate-question",
            }
        ),
        config=config,
    )

    state = graph.get_state(config).values
    generation = store.generation_store.get_by_source_command(
        session_id, "cmd-duplicate-question"
    )
    with store.generation_store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store.generation_store._sql(
                    """
                    SELECT attempt_number, status, last_error_code
                    FROM {attempts}
                    WHERE generation_id = %s
                    ORDER BY attempt_number
                    """
                ),
                (generation.generation_id,),
            )
            attempts = cursor.fetchall()
    assert state["current_index"] == 1
    assert state["messages"][-1]["content"] == "Explain Redis."
    assert all(
        message["content"] != "Explain an API boundary."
        for message in state["messages"][1:]
    )
    assert state["current_followup_count"] == 0
    assert state["termination_reason_code"] == "duplicate_question"
    assert state["termination_diagnostic"]["event_type"] == "followup_terminated"
    assert state["termination_diagnostic"]["command_id"] == "cmd-duplicate-question"
    assert attempts[0] == (1, "failed", "duplicate_question")
    assert examiner.attempt_count == 1

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-duplicate-question",
            }
        ),
        config=config,
    )
    assert examiner.attempt_count == 1


@pytest.mark.pg_runtime
def test_retry_interrupt_waits_for_due_event(durable_graph_table_cleanup):
    graph, config, store, examiner = make_postgres_graph(fail=True)
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    snapshot = graph.get_state(config)
    events = store.control.list_outbox(
        session_id=session_id,
        status="pending",
    )
    event = next(item for item in events if item["event_type"] == "interview_retry_due")
    assert snapshot.next == ("wait_for_retry",)
    assert event["event_type"] == "interview_retry_due"
    assert event["available_at"] > event["created_at"]
    assert examiner.attempt_count == 1

    graph.invoke(
        Command(
            resume={
                "kind": "retry_timer",
                "generation_id": snapshot.values["generation_id"],
                "next_attempt_number": 3,
            }
        ),
        config=config,
    )
    stale = graph.get_state(config)
    assert stale.next == ("wait_for_retry",)
    assert stale.values["generation_attempt"] == 1


@pytest.mark.pg_runtime
def test_third_generation_failure_safely_advances(durable_graph_table_cleanup):
    graph, config, store, examiner = make_postgres_graph(fail=True)
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )
    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )
    generation_id = graph.get_state(config).values["generation_id"]
    for attempt in (2, 3):
        graph.invoke(
            Command(
                resume={
                    "kind": "retry_timer",
                    "generation_id": generation_id,
                    "next_attempt_number": attempt,
                }
            ),
            config=config,
        )

    state = graph.get_state(config).values
    assert state["current_index"] == 1
    assert state["interview_status"] == "active"
    assert state["messages"][-1]["content"] == "Explain Redis."
    assert state["termination_reason_code"] == "generation_retry_exhausted"
    assert state["last_error_code"] == "provider_unavailable"
    assert state["state_version"] == 3
    assert examiner.attempt_count == 3


@pytest.mark.pg_runtime
def test_finish_projects_before_report_job_enqueue(
    durable_graph_table_cleanup,
):
    graph, config, store, _ = make_postgres_graph()
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-finish",
        command_type="finish",
        expected_version=1,
    )

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-finish",
            }
        ),
        config=config,
    )

    assert store.session_snapshot(session_id)["status"] == "finished"
    assert store.session_snapshot(session_id)["state_version"] == 2
    assert store.report_jobs.get_job_by_session(session_id) is not None
