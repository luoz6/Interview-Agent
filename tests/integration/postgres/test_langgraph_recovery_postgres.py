"""PostgreSQL integration coverage."""

from __future__ import annotations
from dataclasses import dataclass
import json
from uuid import uuid4

import pytest
from langgraph.types import Command

from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    build_durable_interview_graph,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.interview_generation_store import (
    ChunkCoalescer,
    PostgresInterviewGenerationStore,
)
from app.services.followup_decision_service import (
    FollowupDecisionExecutionService,
)
from app.services.postgres_decision_store import PostgresDecisionStore
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.langgraph_runtime import PostgresCheckpointerRuntime
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from tests.postgres_support import (
    make_runtime_table_prefix,
    require_postgres_dsn as require_dsn,
)
from tests.integration.postgres.test_postgres_session_store import make_plan


pytestmark = pytest.mark.langgraph_recovery


class InjectedProcessLoss(RuntimeError):
    pass


class StableExaminer:
    def __init__(self, text: str = "replacement complete") -> None:
        self.text = text
        self.calls = 0

    def stream_followup_attempt(self, *, context, execution_context):
        self.calls += 1
        yield self.text


class PartialLossExaminer:
    def stream_followup_attempt(self, *, context, execution_context):
        yield "abandoned partial"
        raise InjectedProcessLoss("after_partial_chunks")


class Delegate:
    def __init__(self, target) -> None:
        self.target = target

    def __getattr__(self, name):
        return getattr(self.target, name)


class FaultingProjectionStore(Delegate):
    def __init__(self, target, *, input_version: int) -> None:
        super().__init__(target)
        self.input_version = input_version
        self.triggered = False

    def project_state(self, state):
        result = self.target.project_state(state)
        if not self.triggered and state["state_version"] == self.input_version:
            self.triggered = True
            raise InjectedProcessLoss("after_projection_write")
        return result


class FaultingGenerationStore(Delegate):
    def __init__(self, target, *, operation: str) -> None:
        super().__init__(target)
        self.operation = operation
        self.triggered = False

    def prepare_generation(self, **kwargs):
        result = self.target.prepare_generation(**kwargs)
        if self.operation == "prepare" and not self.triggered:
            self.triggered = True
            raise InjectedProcessLoss("after_generation_prepare")
        return result

    def complete_attempt(self, *args, **kwargs):
        result = self.target.complete_attempt(*args, **kwargs)
        if self.operation == "complete" and not self.triggered:
            self.triggered = True
            raise InjectedProcessLoss("after_generation_complete")
        return result


class FaultingReportQueue(Delegate):
    def __init__(self, target) -> None:
        super().__init__(target)
        self.triggered = False

    def enqueue_report_request(self, session_id):
        result = self.target.enqueue_report_request(session_id)
        if not self.triggered:
            self.triggered = True
            raise InjectedProcessLoss("after_report_enqueue")
        return result


@dataclass
class RecoveryCase:
    dsn: str
    prefix: str
    session_id: str
    session_store: PostgresInterviewSessionStore
    workflow_store: PostgresInterviewWorkflowStore
    decision_store: PostgresDecisionStore
    generation_store: PostgresInterviewGenerationStore
    report_jobs: PostgresReportJobStore
    plan: object
    checkpointer: PostgresCheckpointerRuntime | None = None
    graph: object | None = None

    @classmethod
    def create(cls):
        dsn = require_dsn()
        prefix = make_runtime_table_prefix("lg_recovery")
        session_id = f"langgraph-recovery-{uuid4().hex}"
        plan = make_plan()
        session_store = PostgresInterviewSessionStore(
            dsn=dsn, table_prefix=prefix
        )
        session_store.insert_durable_session_shell(
            session_id=session_id,
            plan=plan,
            job_description="Backend role",
            resume_text="Built APIs",
            job_tags=["python"],
        )
        return cls(
            dsn=dsn,
            prefix=prefix,
            session_id=session_id,
            session_store=session_store,
            workflow_store=PostgresInterviewWorkflowStore(
                dsn=dsn, table_prefix=prefix
            ),
            decision_store=PostgresDecisionStore(
                dsn=dsn, table_prefix=prefix
            ),
            generation_store=PostgresInterviewGenerationStore(
                dsn=dsn, table_prefix=prefix
            ),
            report_jobs=PostgresReportJobStore(
                dsn=dsn, table_prefix=prefix
            ),
            plan=plan,
        )

    @property
    def config(self):
        return {"configurable": {"thread_id": self.session_id}}

    def open(
        self,
        *,
        workflow_store=None,
        generation_store=None,
        examiner=None,
        report_jobs=None,
        worker_id="recovery-worker",
    ):
        self.close()
        self.checkpointer = PostgresCheckpointerRuntime(self.dsn)
        saver = self.checkpointer.start()
        deps = DurableInterviewGraphDependencies(
            workflow_store=workflow_store or self.workflow_store,
            generation_store=generation_store or self.generation_store,
            decision_service=FollowupDecisionExecutionService(
                store=self.decision_store,
                provider=None,
            ),
            examiner=examiner or StableExaminer(),
            report_job_queue=report_jobs or self.report_jobs,
            coalescer_factory=lambda: ChunkCoalescer(
                max_interval_seconds=0
            ),
            worker_id=worker_id,
        )
        self.graph = build_durable_interview_graph(
            deps, checkpointer=saver
        )
        return self.graph

    def initialize(self):
        graph = self.open()
        graph.invoke(
            make_durable_initial_state(self.session_id, self.plan),
            config=self.config,
        )

    def enqueue(self, command_id: str, command_type="answer"):
        return self.workflow_store.enqueue_command(
            session_id=self.session_id,
            command_id=command_id,
            command_type=command_type,
            expected_version=1,
            answer_text="answer" if command_type == "answer" else None,
        )

    def resume_command(self, command_id: str):
        return self.graph.invoke(
            Command(
                resume={
                    "kind": "answer_command",
                    "command_id": command_id,
                }
            ),
            config=self.config,
        )

    def resume_checkpoint(self):
        return self.graph.invoke(None, config=self.config)

    def expire_attempt(self, generation_id: str, attempt_number: int):
        with self.generation_store._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.generation_store._sql(
                        """
                        UPDATE {attempts}
                        SET lease_expires_at = NOW() - INTERVAL '1 second'
                        WHERE generation_id = %s AND attempt_number = %s
                        """
                    ),
                    (generation_id, attempt_number),
                )

    def close(self):
        if self.checkpointer is not None:
            self.checkpointer.shutdown()
            self.checkpointer = None

    def cleanup(self):
        if self.checkpointer is None:
            self.checkpointer = PostgresCheckpointerRuntime(self.dsn)
            self.checkpointer.start()
        self.checkpointer.delete_thread(self.session_id)
        self.close()


@pytest.fixture
def recovery_case():
    case = RecoveryCase.create()
    try:
        yield case
    finally:
        case.cleanup()


def assert_answer_recovered(case: RecoveryCase, command_id: str):
    state = case.graph.get_state(case.config).values
    assert sum(
        message["role"] == "candidate" and message["content"] == "answer"
        for message in state["messages"]
    ) == 1
    assert sum(
        message["role"] == "interviewer"
        for message in state["messages"]
    ) == 2
    assert case.workflow_store.get_command(
        case.session_id, command_id
    ).status == "applied"
    assert case.session_store.get(case.session_id)["state_version"] == 3


@pytest.mark.parametrize(
    "fault_point",
    [
        "after_command_commit",
        "after_candidate_projection",
        "after_generation_prepare",
        "after_partial_chunks",
        "after_generation_complete",
        "after_projection_write",
        "after_report_enqueue",
    ],
)
def test_restart_recovers_without_duplicate_business_output(
    recovery_case, fault_point
):
    case = recovery_case
    case.initialize()
    command_id = f"cmd-{fault_point}"
    if fault_point == "after_report_enqueue":
        case.enqueue(command_id, command_type="finish")
        case.open(report_jobs=FaultingReportQueue(case.report_jobs))
        with pytest.raises(InjectedProcessLoss):
            case.resume_command(command_id)
        case.open()
        case.resume_checkpoint()
        assert case.report_jobs.count_jobs() == 1
        assert case.workflow_store.get_command(
            case.session_id, command_id
        ).status == "applied"
        return

    case.enqueue(command_id)
    if fault_point == "after_command_commit":
        case.open()
        case.resume_command(command_id)
    elif fault_point == "after_candidate_projection":
        faulting = FaultingProjectionStore(
            case.workflow_store, input_version=1
        )
        case.open(workflow_store=faulting)
        with pytest.raises(InjectedProcessLoss):
            case.resume_command(command_id)
        case.open()
        case.resume_checkpoint()
    elif fault_point == "after_generation_prepare":
        faulting = FaultingGenerationStore(
            case.generation_store, operation="prepare"
        )
        case.open(generation_store=faulting)
        with pytest.raises(InjectedProcessLoss):
            case.resume_command(command_id)
        case.open()
        case.resume_checkpoint()
    elif fault_point == "after_partial_chunks":
        case.open(
            examiner=PartialLossExaminer(), worker_id="lost-worker"
        )
        with pytest.raises(InjectedProcessLoss):
            case.resume_command(command_id)
        generation_id = case.graph.get_state(case.config).values[
            "generation_id"
        ]
        case.expire_attempt(generation_id, 1)
        case.open(worker_id="replacement-worker")
        case.resume_checkpoint()
        events = case.generation_store.list_events(generation_id)
        reset_index = next(
            index
            for index, event in enumerate(events)
            if event.event_type == "generation_reset"
        )
        assert events[reset_index].attempt_number == 2
        assert events[reset_index + 1].attempt_number == 2
    elif fault_point == "after_generation_complete":
        faulting = FaultingGenerationStore(
            case.generation_store, operation="complete"
        )
        case.open(generation_store=faulting)
        with pytest.raises(InjectedProcessLoss):
            case.resume_command(command_id)
        case.open()
        case.resume_checkpoint()
    else:
        faulting = FaultingProjectionStore(
            case.workflow_store, input_version=2
        )
        case.open(workflow_store=faulting)
        with pytest.raises(InjectedProcessLoss):
            case.resume_command(command_id)
        case.open()
        case.resume_checkpoint()

    assert_answer_recovered(case, command_id)


def test_retry_event_uses_database_due_time(recovery_case):
    case = recovery_case
    case.initialize()
    schedule = case.workflow_store.enqueue_retry(
        session_id=case.session_id,
        generation_id="gen-timer",
        next_attempt_number=2,
        delay_seconds=30,
    )

    assert case.workflow_store.control.claim_batch(
        worker_id="early-worker", limit=1, lease_seconds=60
    ) == []
    with case.workflow_store.control.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                case.workflow_store._sql(
                    "UPDATE {outbox} SET available_at = NOW() - INTERVAL '1 second' WHERE event_id = %s"
                ),
                (schedule.event_id,),
            )
    claims = case.workflow_store.control.claim_batch(
        worker_id="due-worker", limit=1, lease_seconds=60
    )
    assert [claim["event_id"] for claim in claims] == [schedule.event_id]


def test_explicit_purge_deletes_checkpoint_and_generation_rows(recovery_case):
    case = recovery_case
    case.initialize()
    generation = case.generation_store.prepare_generation(
        session_id=case.session_id,
        source_command_id="cmd-purge",
        question_id="q1",
    )
    assert case.generation_store.count_session_rows(case.session_id) == 1

    case.checkpointer.delete_thread(case.session_id)
    case.workflow_store.delete_session_control_rows(case.session_id)
    case.generation_store.delete_session_rows(case.session_id)

    assert case.checkpointer.saver.get(case.config) is None
    assert case.generation_store.count_session_rows(case.session_id) == 0
    assert generation.generation_id


def test_recovery_diagnostics_are_metadata_only(monkeypatch):
    from app.api.runtime.routes import runtime_boundary

    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", "0")
    serialized = json.dumps(runtime_boundary(), ensure_ascii=False)
    for forbidden in (
        "job_description",
        "resume_text",
        "answer_text",
        "provider_payload",
        "lease_owner",
        "checkpoint_id",
    ):
        assert forbidden not in serialized
