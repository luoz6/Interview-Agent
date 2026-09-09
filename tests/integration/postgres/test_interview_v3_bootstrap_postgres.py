from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.adapters.postgres.owned_scope import Psycopg2OwnedScopeBackend
from app.domain.interview.question_intent import QuestionIntentV1
from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    build_durable_interview_graph_v3,
)
from app.services.interview_generation_store import (
    PostgresInterviewGenerationStore,
)
from app.services.interview_plan_revision import (
    InterviewPlanV3,
    default_plan_configuration,
    legacy_interview_knowledge_scope_snapshot,
    plan_payload_sha256,
)
from app.services.interview_workflow import InterviewWorkflowService
from app.services.interview_workflow_consumer import InterviewWorkflowConsumer
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.langgraph_runtime import (
    PostgresCheckpointerRuntime,
    VersionedGraphRegistry,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.runtime_domain_events import InterviewBootstrapReadyEvent
from app.services.session_plan_binding import SessionPlanBinding
from tests.postgres_v3_checkpointer_support import (
    AUTHORIZED_RELATIONS,
    AuthorizedCheckpointRowScope,
    CheckpointerRecoveryNotEnabled,
    OPT_IN_ENV,
    load_checkpointer_recovery_authorization,
)


pytestmark = pytest.mark.pg_runtime


@pytest.fixture
def v3_checkpointer_opt_in():
    environment = dict(os.environ)
    if environment.get(OPT_IN_ENV, "").strip() != "1":
        pytest.skip(f"set {OPT_IN_ENV}=1 to enable checkpointer recovery")
    return environment


@pytest.fixture
def authorized_v3_checkpointer_scope(
    v3_checkpointer_opt_in,
    postgres_dsn,
    runtime_table_prefix,
):
    provider = DirectPsycopg2ConnectionProvider(postgres_dsn)
    identity = Psycopg2OwnedScopeBackend(provider).inspect_identity()
    try:
        receipt = load_checkpointer_recovery_authorization(
            v3_checkpointer_opt_in,
            current_target_fingerprint=identity.fingerprint,
            current_database=identity.database_name,
        )
    except CheckpointerRecoveryNotEnabled:
        pytest.skip(f"set {OPT_IN_ENV}=1 to enable checkpointer recovery")
    return (
        postgres_dsn,
        runtime_table_prefix,
        provider,
        AuthorizedCheckpointRowScope(receipt),
    )


def _frozen_v3_plan() -> tuple[InterviewPlanV3, SessionPlanBinding]:
    plan = InterviewPlanV3(
        title="V3 PostgreSQL bootstrap",
        configuration_snapshot=default_plan_configuration(),
        knowledge_scope=legacy_interview_knowledge_scope_snapshot(),
        questions=(
            QuestionIntentV1(
                question_id="q1",
                position=1,
                kind="project",
                focus="Redis 库存一致性",
                difficulty="advanced",
                assessment_goals=("failure_mode", "recovery", "tradeoff"),
                expected_minutes=5,
                expected_followups=1,
                knowledge_binding={},
            ),
        ),
    )
    binding = SessionPlanBinding(
        plan_origin="plan_revision",
        plan_revision_id="00000000-0000-4000-8000-000000000101",
        plan_family_id="00000000-0000-4000-8000-000000000102",
        revision=1,
        plan_sha256=plan_payload_sha256(plan),
        configuration_snapshot=plan.configuration_snapshot.model_dump(
            mode="json"
        ),
        plan_snapshot=plan.model_dump(mode="json"),
    )
    return plan, binding


def _postgres_stores(postgres_dsn: str, runtime_table_prefix: str):
    session_store = PostgresInterviewSessionStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    return session_store, workflow_store


def _workflow(
    session_store,
    workflow_store,
    *,
    graph,
    generation_store=None,
) -> InterviewWorkflowService:
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v3", graph)
    return InterviewWorkflowService(
        legacy_store=session_store,
        workflow_store=workflow_store,
        generation_store=generation_store or object(),
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v3",
    )


def _start_v3(workflow, plan, binding, session_id: str):
    return workflow.start(
        plan,
        job_description="Backend engineer",
        resume_text="Built Redis and RocketMQ recovery workflows",
        job_tags=["Redis", "RocketMQ"],
        plan_binding=binding,
        session_id=session_id,
        bootstrap=False,
    )


def test_v3_shell_binding_and_bootstrap_outbox_commit_atomically(
    postgres_dsn,
    runtime_table_prefix,
):
    session_store, workflow_store = _postgres_stores(
        postgres_dsn, runtime_table_prefix
    )
    plan, binding = _frozen_v3_plan()
    workflow = _workflow(
        session_store,
        workflow_store,
        graph=object(),
    )
    committed_session_id = f"v3-commit-{uuid4().hex}"

    _start_v3(workflow, plan, binding, committed_session_id)

    state = session_store.get(committed_session_id)
    assert state["workflow_engine"] == "langgraph-v3"
    assert state["graph_schema_version"] == "langgraph-v3"
    assert state["status"] == "preparing_first_question"
    assert state["state_version"] == state["checkpoint_version"] == 0
    assert state["messages"] == []
    assert state["plan_revision_id"] == binding.plan_revision_id
    assert state["plan_sha256"] == binding.plan_sha256
    assert state["configuration_snapshot"] == binding.configuration_snapshot
    assert state["plan_snapshot"] == binding.plan_snapshot
    assert "prompt" not in state["plan_snapshot"]["questions"][0]
    events = workflow_store.control.list_outbox(
        session_id=committed_session_id
    )
    assert len(events) == 1
    assert events[0]["event_id"] == (
        f"interview-bootstrap-{committed_session_id}"
    )
    assert events[0]["event_type"] == "interview_bootstrap_ready"

    rollback_session_id = f"v3-rollback-{uuid4().hex}"
    enqueue = workflow_store.enqueue_bootstrap_with_cursor

    def enqueue_then_fail(cursor, session_id):
        enqueue(cursor, session_id)
        raise RuntimeError("injected failure after bootstrap enqueue")

    workflow_store.enqueue_bootstrap_with_cursor = enqueue_then_fail
    with pytest.raises(RuntimeError, match="injected failure"):
        _start_v3(workflow, plan, binding, rollback_session_id)

    with pytest.raises(ValueError, match="session not found"):
        session_store.get(rollback_session_id)
    assert workflow_store.control.count_outbox(
        f"interview-bootstrap-{rollback_session_id}"
    ) == 0


class _CountingExaminer:
    def __init__(self) -> None:
        self.calls = 0

    def generate_main_question_attempt(self, **kwargs) -> str:
        self.calls += 1
        return "如果 Redis 库存扣减成功但消息投递失败，你会如何恢复？"


class _FailFirstProjection:
    def __init__(self, workflow_store) -> None:
        self.workflow_store = workflow_store
        self.calls = 0

    def __call__(self, state):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("injected project failure")
        return self.workflow_store.project_state(state)


def _build_v3_graph(
    *,
    checkpointer,
    workflow_store,
    generation_store,
    examiner,
    project_state=None,
):
    return build_durable_interview_graph_v3(
        DurableInterviewGraphDependencies(
            workflow_store=workflow_store,
            project_state=project_state,
            generation_store=generation_store,
            examiner=examiner,
        ),
        checkpointer=checkpointer,
    )


def test_v3_completed_generation_survives_projection_failure_and_redelivery(
    authorized_v3_checkpointer_scope,
):
    postgres_dsn, prefix, provider, row_scope = authorized_v3_checkpointer_scope
    session_id = f"test_jit_v3_{uuid4().hex}"
    assert row_scope.inventory_for_write(provider, session_id) == {
        relation: 0 for relation in AUTHORIZED_RELATIONS
    }
    session_store = PostgresInterviewSessionStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    plan, binding = _frozen_v3_plan()
    event = InterviewBootstrapReadyEvent(
        event_id=f"interview-bootstrap-{session_id}",
        session_id=session_id,
    ).model_dump(mode="json")
    examiner = _CountingExaminer()
    runtimes = []
    try:
        first_runtime = PostgresCheckpointerRuntime(postgres_dsn)
        runtimes.append(first_runtime)
        row_scope.assert_write_authorized(session_id)
        failed_graph = _build_v3_graph(
            checkpointer=first_runtime.start(),
            workflow_store=workflow_store,
            generation_store=generation_store,
            examiner=examiner,
            project_state=_FailFirstProjection(workflow_store),
        )
        failed_workflow = _workflow(
            session_store,
            workflow_store,
            graph=failed_graph,
            generation_store=generation_store,
        )
        _start_v3(failed_workflow, plan, binding, session_id)

        row_scope.assert_write_authorized(session_id)
        with pytest.raises(RuntimeError, match="injected project failure"):
            InterviewWorkflowConsumer(failed_workflow).consume(event)

        completed = generation_store.get_by_source_command(
            session_id, "bootstrap"
        )
        assert completed is not None
        assert completed.status == "completed"
        assert completed.provider_invocation_count == 1
        assert examiner.calls == 1

        first_runtime.shutdown()
        second_runtime = PostgresCheckpointerRuntime(postgres_dsn)
        runtimes.append(second_runtime)
        checkpoint_rows = row_scope.inventory_for_write(provider, session_id)
        assert sum(checkpoint_rows.values()) > 0
        recovered_graph = _build_v3_graph(
            checkpointer=second_runtime.start(),
            workflow_store=workflow_store,
            generation_store=generation_store,
            examiner=examiner,
        )
        recovered_workflow = _workflow(
            session_store,
            workflow_store,
            graph=recovered_graph,
            generation_store=generation_store,
        )
        consumer = InterviewWorkflowConsumer(recovered_workflow)

        row_scope.assert_write_authorized(session_id)
        assert consumer.consume(event).status == "completed"
        row_scope.assert_write_authorized(session_id)
        assert consumer.consume(event).status == "completed"

        recovered = generation_store.get_by_source_command(
            session_id, "bootstrap"
        )
        assert recovered is not None
        assert recovered.generation_id == completed.generation_id
        assert generation_store.count_session_rows(session_id) == 1
        assert examiner.calls == 1
        assert workflow_store.count_messages(session_id) == 1
        assert workflow_store.session_snapshot(session_id)["status"] == "active"
        graph_state = recovered_graph.get_state(
            {"configurable": {"thread_id": session_id}}
        )
        assert graph_state.next == ("wait_for_answer",)
    finally:
        try:
            for runtime in reversed(runtimes):
                runtime.shutdown()
        finally:
            assert row_scope.cleanup_and_inventory(provider, session_id) == {
                relation: 0 for relation in AUTHORIZED_RELATIONS
            }
