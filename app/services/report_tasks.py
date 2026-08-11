from __future__ import annotations

from app.services.agent_runtime import AgentExecutionRunner
from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
)
from app.services.report_pipeline import ReportGenerationPipeline
from app.services.runtime import (
    get_agent_execution_runner,
    resolve_runtime_llm,
)
from app.services.session import InterviewSessionStore
from app.adapters.pgvector.repository import get_knowledge_store


def execute_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
):
    return ReportGenerationPipeline().execute(
        session_id=session_id,
        store=store,
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
        attempt_number=attempt_number,
    )


def run_report_generation(
    session_id: str,
    store: InterviewSessionStore,
    llm,
    vector_store,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
):
    try:
        return execute_report_generation(
            session_id=session_id,
            store=store,
            llm=llm,
            vector_store=vector_store,
            execution_runner=execution_runner,
            attempt_number=attempt_number,
        )
    except ValueError as exc:
        if str(exc) == "session not found":
            return None
        store.fail_report(session_id, str(exc))
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        store.fail_report(session_id, str(exc))
    return None


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    try:
        vector_store = get_knowledge_store()
        llm = resolve_runtime_llm(store)
        execution_runner = get_agent_execution_runner()
    except Exception as exc:
        try:
            store.fail_report(session_id, str(exc))
        except ValueError as store_exc:
            if str(store_exc) != "session not found":
                raise
        return

    run_report_generation(
        session_id=session_id,
        store=store,
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
    )


__all__ = [
    "execute_report_generation",
    "generate_report_for_session",
    "run_report_generation",
]
