import os
import socket
from dataclasses import dataclass
from uuid import uuid4

from app.services.config import (
    DEFAULT_POSTGRES_DSN,
    get_postgres_dsn,
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
    get_report_langgraph_runtime_enabled,
    get_report_langgraph_version,
    get_runtime_event_backend,
    get_runtime_outbox_batch_size,
    get_runtime_outbox_lease_seconds,
    get_runtime_outbox_poll_seconds,
    get_runtime_store,
    get_runtime_table_prefix,
)
from app.services.agent_recorders import (
    CompositeAgentRunRecorder,
    PostgresAgentRunRecorder,
)
from app.services.agent_runtime import AgentExecutionRunner
from app.services.agent_trace import AgentTraceRecorder
from app.services.drafts import AnonymousDraftStore
from app.services.llm import InterviewLLM, OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.runtime_outbox_dispatcher import (
    CeleryRuntimeEventSink,
    LocalRuntimeEventSink,
    RuntimeOutboxDispatcher,
    RuntimeOutboxService,
)
from app.services.session import InterviewSessionStore
from app.services.vector_store import PgVectorKnowledgeStore, get_knowledge_store


@dataclass(frozen=True)
class ReportExecutor:
    store: InterviewSessionStore
    llm: InterviewLLM
    vector_store: PgVectorKnowledgeStore
    execution_runner: AgentExecutionRunner | None = None


_session_store = None
_report_job_store = None
_report_executor = None
_draft_store = None
_event_publisher = None
_runtime_control_store = None
_runtime_outbox_service = None
_agent_execution_runner = None
_agent_composite_recorder = None
_agent_postgres_control_ids: set[int] = set()
_langgraph_checkpointer_runtime = None
_langgraph_checkpointer_started = False
_interview_workflow_service = None
_interview_workflow_consumer = None
_review_workflow_service = None
_review_workflow_consumer = None


def build_session_store(llm=None):
    store_kind = get_runtime_store()
    execution_runner = get_agent_execution_runner()
    if store_kind == "postgres":
        store = PostgresInterviewSessionStore(
            dsn=get_postgres_dsn(),
            table_prefix=get_runtime_table_prefix(),
            llm=llm,
            execution_runner=execution_runner,
        )
        control_store = getattr(store, "_runtime_control", None)
        if control_store is not None:
            get_agent_execution_runner(control_store=control_store)
        return store
    if store_kind != "memory":
        raise RuntimeError(f"unsupported INTERVIEW_RUNTIME_STORE: {store_kind}")
    return InterviewSessionStore(
        llm=llm,
        execution_runner=execution_runner,
    )


def build_report_job_store():
    return PostgresReportJobStore(
        dsn=get_postgres_dsn(),
        table_prefix=get_runtime_table_prefix(),
        lease_seconds=int(os.getenv("REPORT_JOB_LEASE_SECONDS", "300")),
    )


def build_draft_store():
    return AnonymousDraftStore()


def build_event_publisher():
    from app.services.event_publisher import (
        LocalRoundReviewEventPublisher,
        NoopRuntimeEventPublisher,
    )

    backend = get_runtime_event_backend()
    if backend == "local":
        return LocalRoundReviewEventPublisher()
    if backend == "noop":
        return NoopRuntimeEventPublisher()
    if backend == "celery":
        try:
            from app.services.celery_app import celery_app
            from app.services.event_publisher import CeleryRuntimeEventPublisher
        except ImportError as exc:
            raise RuntimeError(
                "INTERVIEW_EVENT_BACKEND=celery requires runtime event components"
            ) from exc
        return CeleryRuntimeEventPublisher(celery_app=celery_app)
    raise RuntimeError(f"unsupported INTERVIEW_EVENT_BACKEND: {backend}")


def build_report_executor(
    *,
    store: InterviewSessionStore | None = None,
    llm: InterviewLLM | None = None,
    vector_store: PgVectorKnowledgeStore | None = None,
) -> ReportExecutor:
    resolved_store = store or get_session_store()
    resolved_llm = resolve_runtime_llm(resolved_store, llm)
    resolved_vector_store = vector_store or get_knowledge_store()
    return ReportExecutor(
        store=resolved_store,
        llm=resolved_llm,
        vector_store=resolved_vector_store,
        execution_runner=get_agent_execution_runner(),
    )


def resolve_runtime_llm(
    store: InterviewSessionStore,
    llm: InterviewLLM | None = None,
) -> InterviewLLM:
    return llm or store.llm or OpenAIInterviewLLM()


def get_session_store():
    global _session_store
    if _session_store is None:
        _session_store = build_session_store()
    return _session_store


def get_report_job_store():
    global _report_job_store
    if _report_job_store is None:
        _report_job_store = build_report_job_store()
    return _report_job_store


def get_draft_store():
    global _draft_store
    if _draft_store is None:
        _draft_store = build_draft_store()
    return _draft_store


def get_event_publisher():
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = build_event_publisher()
    return _event_publisher


def get_runtime_control_store():
    global _runtime_control_store
    if _runtime_control_store is None:
        if get_runtime_store() != "postgres":
            return None
        session_store = get_session_store()
        _runtime_control_store = session_store._runtime_control
    return _runtime_control_store


def get_langgraph_checkpointer_runtime():
    global _langgraph_checkpointer_runtime
    if get_runtime_store() != "postgres":
        return None
    if not (
        get_interview_langgraph_runtime_enabled()
        or get_report_langgraph_runtime_enabled()
    ):
        return None
    if _langgraph_checkpointer_runtime is None:
        from app.services.langgraph_runtime import PostgresCheckpointerRuntime

        _langgraph_checkpointer_runtime = PostgresCheckpointerRuntime(
            get_postgres_dsn()
        )
    return _langgraph_checkpointer_runtime


def build_interview_workflow_service():
    from app.agents.examiner import ExaminerAgent
    from app.graphs.durable_interview_graph import (
        DurableInterviewGraphDependencies,
        build_durable_interview_graph,
    )
    from app.services.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.services.interview_workflow import InterviewWorkflowService
    from app.services.interview_workflow_store import (
        PostgresInterviewWorkflowStore,
    )
    from app.services.langgraph_runtime import (
        VersionedGraphRegistry,
    )

    if get_runtime_store() != "postgres":
        raise RuntimeError("durable interview workflow requires PostgreSQL")
    checkpointer = get_langgraph_checkpointer_runtime()
    if checkpointer is None:
        raise RuntimeError("LangGraph runtime is disabled")
    saver = checkpointer.start()
    store = get_session_store()
    dsn = get_postgres_dsn()
    prefix = get_runtime_table_prefix()
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=dsn, table_prefix=prefix
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=dsn, table_prefix=prefix
    )
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        examiner=ExaminerAgent(
            llm=store.llm,
            execution_runner=get_agent_execution_runner(),
        ),
        knowledge_repository=get_knowledge_store(),
        report_job_queue=get_report_job_store(),
    )
    graph = build_durable_interview_graph(deps, checkpointer=saver)
    registry = VersionedGraphRegistry()
    version = get_interview_langgraph_version()
    registry.register(version, graph)
    return InterviewWorkflowService(
        legacy_store=store,
        workflow_store=workflow_store,
        generation_store=generation_store,
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=get_interview_langgraph_runtime_enabled(),
        rollout_percent=get_interview_langgraph_rollout_percent(),
        default_graph_version=version,
    )


def get_interview_workflow_service():
    global _interview_workflow_service
    if _interview_workflow_service is None:
        _interview_workflow_service = build_interview_workflow_service()
    return _interview_workflow_service


def get_interview_workflow_consumer():
    global _interview_workflow_consumer
    if _interview_workflow_consumer is None:
        from app.services.interview_workflow_consumer import (
            InterviewWorkflowConsumer,
        )

        _interview_workflow_consumer = InterviewWorkflowConsumer(
            get_interview_workflow_service()
        )
    return _interview_workflow_consumer


def build_review_workflow_service():
    from app.agents.report_coach import ReportCoachAgent
    from app.graphs.durable_review_graph import (
        DurableReviewGraphDependencies,
        build_durable_review_graph,
    )
    from app.services.agent_runtime import AgentExecutionContext, correlation_id_from_plan
    from app.services.report_microbatch import build_report_coach_items_from_question_evaluations
    from app.services.report_runtime_quality import evaluate_runtime_report_quality
    from app.services.review_workflow import ReviewWorkflowService
    from app.services.review_workflow_store import PostgresReviewWorkflowStore
    from app.services.round_review_runner import evaluate_round_review_event
    from app.services.runtime_domain_events import RoundClosedEvent
    from app.services.langgraph_runtime import VersionedGraphRegistry

    checkpointer = get_langgraph_checkpointer_runtime()
    if checkpointer is None:
        raise RuntimeError("LangGraph runtime is disabled")
    store = get_session_store()
    workflow_store = PostgresReviewWorkflowStore(
        dsn=get_postgres_dsn(), table_prefix=get_runtime_table_prefix()
    )
    runner = get_agent_execution_runner()
    vector_store = get_knowledge_store()

    def review_question(graph_state, question_id):
        state = store.get(graph_state["session_id"])
        question = next(item for item in graph_state["review_input_manifest"]["questions"] if item["question_id"] == question_id)
        record = evaluate_round_review_event(
            RoundClosedEvent(
                session_id=state["session_id"], question_id=question_id,
                answer_state=question["answer_state"], job_tags=list(state["job_tags"]),
                state_version=state["state_version"],
            ), state=state, llm=resolve_runtime_llm(store), vector_store=vector_store,
            execution_runner=runner, attempt_number=graph_state["provider_attempt"],
        ).model_copy(update={
            "review_input_sha256": graph_state["review_input_manifest"]["input_sha256"],
            "question_input_sha256": question["input_sha256"],
            "review_engine": "langgraph-review-v1",
            "review_graph_schema_version": graph_state["review_graph_schema_version"],
        })
        store.upsert_question_evaluation(state["session_id"], record)

    def generate_report(graph_state):
        state = store.get(graph_state["session_id"])
        records = store.list_question_evaluations(state["session_id"])
        report = ReportCoachAgent(llm=resolve_runtime_llm(store), execution_runner=runner).generate_report_attempt(
            plan=state["plan"],
            evaluation_items=build_report_coach_items_from_question_evaluations(records),
            session_id=state["session_id"],
            execution_context=AgentExecutionContext(
                correlation_id=correlation_id_from_plan(state["plan"], session_id=state["session_id"]),
                agent="report_coach", operation="generate_durable_report", phase="review",
                session_id=state["session_id"], attempt_number=graph_state["provider_attempt"],
            ),
        )
        return workflow_store.save_report_artifact(job_id=graph_state["job_id"], report=report)

    def validate_report(graph_state):
        report = workflow_store.load_report_artifact(graph_state["job_id"])
        expected = len(graph_state["review_input_manifest"]["questions"])
        return "passed" if not evaluate_runtime_report_quality(report, expected_question_count=expected).blocking_issues else "failed"

    def commit_report(graph_state):
        workflow_store.commit_report(job_id=graph_state["job_id"], report=workflow_store.load_report_artifact(graph_state["job_id"]))

    deps = DurableReviewGraphDependencies(workflow_store=workflow_store, review_question=review_question, generate_report=generate_report, validate_report=validate_report, commit_report=commit_report)
    version = get_report_langgraph_version()
    registry = VersionedGraphRegistry()
    registry.register(version, build_durable_review_graph(deps, checkpointer=checkpointer.start()))
    return ReviewWorkflowService(session_store=store, workflow_store=workflow_store, graph_registry=registry, checkpointer_runtime=checkpointer)


def get_review_workflow_service():
    global _review_workflow_service
    if _review_workflow_service is None:
        _review_workflow_service = build_review_workflow_service()
    return _review_workflow_service


def get_review_workflow_consumer():
    global _review_workflow_consumer
    if _review_workflow_consumer is None:
        from app.services.review_workflow_consumer import ReviewWorkflowConsumer
        _review_workflow_consumer = ReviewWorkflowConsumer(
            get_review_workflow_service(), get_report_job_store()
        )
    return _review_workflow_consumer


def get_agent_execution_runner(
    *,
    control_store=None,
) -> AgentExecutionRunner:
    global _agent_execution_runner, _agent_composite_recorder
    if _agent_execution_runner is None:
        _agent_composite_recorder = CompositeAgentRunRecorder(
            [AgentTraceRecorder.from_env()]
        )
        _agent_execution_runner = AgentExecutionRunner(
            recorder=_agent_composite_recorder
        )
    if (
        control_store is not None
        and id(control_store) not in _agent_postgres_control_ids
    ):
        _agent_composite_recorder.add_recorder(
            PostgresAgentRunRecorder(control_store)
        )
        _agent_postgres_control_ids.add(id(control_store))
    return _agent_execution_runner


def build_runtime_outbox_service() -> RuntimeOutboxService:
    control_store = get_runtime_control_store()
    if control_store is None:
        raise RuntimeError("runtime outbox requires PostgreSQL")
    worker_id = _runtime_worker_id("local")
    sink = LocalRuntimeEventSink(
        control_store=control_store,
        worker_id=f"{worker_id}:consumer",
        store=get_session_store(),
        interview_consumer=get_interview_workflow_consumer(),
        review_consumer=get_review_workflow_consumer(),
    )
    return RuntimeOutboxService(
        RuntimeOutboxDispatcher(
            control_store,
            sink,
            batch_size=get_runtime_outbox_batch_size(),
            lease_seconds=get_runtime_outbox_lease_seconds(),
        ),
        worker_id=worker_id,
        poll_seconds=get_runtime_outbox_poll_seconds(),
    )


def build_celery_runtime_outbox_service() -> RuntimeOutboxService:
    from app.services.celery_app import celery_app

    control_store = get_runtime_control_store()
    if control_store is None:
        raise RuntimeError("runtime outbox requires PostgreSQL")
    worker_id = _runtime_worker_id("celery")
    return RuntimeOutboxService(
        RuntimeOutboxDispatcher(
            control_store,
            CeleryRuntimeEventSink(celery_app=celery_app),
            batch_size=get_runtime_outbox_batch_size(),
            lease_seconds=get_runtime_outbox_lease_seconds(),
        ),
        worker_id=worker_id,
        poll_seconds=get_runtime_outbox_poll_seconds(),
    )


def start_runtime() -> None:
    global _langgraph_checkpointer_runtime, _langgraph_checkpointer_started
    global _review_workflow_service
    global _review_workflow_consumer
    global _interview_workflow_service, _interview_workflow_consumer
    global _runtime_outbox_service
    if get_runtime_store() != "postgres":
        return
    if _langgraph_checkpointer_runtime is None:
        _langgraph_checkpointer_runtime = get_langgraph_checkpointer_runtime()
    checkpointer_runtime = _langgraph_checkpointer_runtime
    if checkpointer_runtime is not None and not _langgraph_checkpointer_started:
        checkpointer_runtime.start()
        _langgraph_checkpointer_started = True
    if get_runtime_event_backend() != "local":
        return
    if _runtime_outbox_service is None:
        _runtime_outbox_service = build_runtime_outbox_service()
        _runtime_outbox_service.start()


def get_report_executor():
    global _report_executor
    if _report_executor is None:
        _report_executor = build_report_executor()
    return _report_executor


def shutdown_runtime(*, wait: bool = True) -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store
    global _event_publisher, _runtime_control_store, _runtime_outbox_service
    global _agent_execution_runner, _agent_composite_recorder
    global _langgraph_checkpointer_runtime, _langgraph_checkpointer_started
    if _runtime_outbox_service is not None:
        _runtime_outbox_service.shutdown(wait=wait)
    if _langgraph_checkpointer_runtime is not None:
        _langgraph_checkpointer_runtime.shutdown()
    _shutdown_cached_publisher(_event_publisher, wait=wait)
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
    _event_publisher = None
    _runtime_control_store = None
    _runtime_outbox_service = None
    _langgraph_checkpointer_runtime = None
    _langgraph_checkpointer_started = False
    _interview_workflow_service = None
    _interview_workflow_consumer = None
    _review_workflow_service = None
    _review_workflow_consumer = None
    _agent_execution_runner = None
    _agent_composite_recorder = None
    _agent_postgres_control_ids.clear()


def reset_runtime_for_tests() -> None:
    shutdown_runtime(wait=False)


def _shutdown_cached_publisher(publisher, *, wait: bool) -> None:
    if publisher is None:
        return
    shutdown = getattr(publisher, "shutdown", None)
    if shutdown is not None:
        shutdown(wait=wait)


def _runtime_worker_id(mode: str) -> str:
    return (
        f"runtime-{mode}@{socket.gethostname()}-"
        f"{uuid4().hex[:12]}"
    )
