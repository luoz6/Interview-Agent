import os
import socket
from dataclasses import dataclass
from uuid import uuid4

from app.services.config import (
    DEFAULT_POSTGRES_DSN,
    get_postgres_dsn,
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
    global _runtime_outbox_service
    if (
        get_runtime_store() != "postgres"
        or get_runtime_event_backend() != "local"
    ):
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
    if _runtime_outbox_service is not None:
        _runtime_outbox_service.shutdown(wait=wait)
    _shutdown_cached_publisher(_event_publisher, wait=wait)
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
    _event_publisher = None
    _runtime_control_store = None
    _runtime_outbox_service = None
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
