import os
from dataclasses import dataclass

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


def build_session_store(llm=None):
    store_kind = os.getenv("INTERVIEW_RUNTIME_STORE", "memory").strip().lower()
    if store_kind == "postgres":
        dsn = os.getenv("POSTGRES_DSN")
        if not dsn:
            raise RuntimeError("POSTGRES_DSN is required when INTERVIEW_RUNTIME_STORE=postgres")
        return PostgresInterviewSessionStore(
            dsn=dsn,
            table_prefix=os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview"),
            llm=llm,
        )
    if store_kind != "memory":
        raise RuntimeError(f"unsupported INTERVIEW_RUNTIME_STORE: {store_kind}")
    return InterviewSessionStore(llm=llm)


def build_report_job_store():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required to build report job store")
    return PostgresReportJobStore(
        dsn=dsn,
        table_prefix=os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview"),
        lease_seconds=int(os.getenv("REPORT_JOB_LEASE_SECONDS", "300")),
    )


def build_draft_store():
    return AnonymousDraftStore()


def build_report_executor(
    *,
    store: InterviewSessionStore | None = None,
    llm: InterviewLLM | None = None,
    vector_store: PgVectorKnowledgeStore | None = None,
) -> ReportExecutor:
    resolved_store = store or get_session_store()
    resolved_llm = llm or resolved_store.llm or OpenAIInterviewLLM()
    resolved_vector_store = vector_store or get_knowledge_store()
    return ReportExecutor(
        store=resolved_store,
        llm=resolved_llm,
        vector_store=resolved_vector_store,
    )


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


def get_report_executor():
    global _report_executor
    if _report_executor is None:
        _report_executor = build_report_executor()
    return _report_executor


def reset_runtime_for_tests() -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
