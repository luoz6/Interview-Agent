import os
from dataclasses import dataclass

from app.services.config import (
    DEFAULT_POSTGRES_DSN,
    get_postgres_dsn,
    get_runtime_event_backend,
    get_runtime_store,
    get_runtime_table_prefix,
)
from app.services.drafts import AnonymousDraftStore
from app.services.llm import InterviewLLM, OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.session import InterviewSessionStore
from app.services.vector_store import PgVectorKnowledgeStore, get_knowledge_store


@dataclass(frozen=True)
class ReportExecutor:
    store: InterviewSessionStore
    llm: InterviewLLM
    vector_store: PgVectorKnowledgeStore


_session_store = None
_report_job_store = None
_report_executor = None
_draft_store = None
_event_publisher = None


def build_session_store(llm=None):
    store_kind = get_runtime_store()
    if store_kind == "postgres":
        return PostgresInterviewSessionStore(
            dsn=get_postgres_dsn(),
            table_prefix=get_runtime_table_prefix(),
            llm=llm,
        )
    if store_kind != "memory":
        raise RuntimeError(f"unsupported INTERVIEW_RUNTIME_STORE: {store_kind}")
    return InterviewSessionStore(llm=llm)


def build_report_job_store():
    return PostgresReportJobStore(
        dsn=get_postgres_dsn(),
        table_prefix=get_runtime_table_prefix(),
        lease_seconds=int(os.getenv("REPORT_JOB_LEASE_SECONDS", "300")),
    )


def build_draft_store():
    return AnonymousDraftStore()


def build_event_publisher():
    from app.services.event_publisher import NoopRuntimeEventPublisher

    backend = get_runtime_event_backend()
    if backend == "noop":
        return NoopRuntimeEventPublisher()
    if backend == "celery":
        try:
            from app.services.celery_app import celery_app
            from app.services.event_publisher import CeleryRuntimeEventPublisher
        except ImportError as exc:  # pragma: no cover - exercised after Task 2 lands
            raise RuntimeError(
                "INTERVIEW_EVENT_BACKEND=celery requires Stage 26A Task 2 runtime event components"
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


def get_report_executor():
    global _report_executor
    if _report_executor is None:
        _report_executor = build_report_executor()
    return _report_executor


def reset_runtime_for_tests() -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store, _event_publisher
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
    _event_publisher = None
