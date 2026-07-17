from collections.abc import Callable

from fastapi import BackgroundTasks

from app.ports.runtime import ReportJobQueue, ReportRepository
from app.services.report_tasks import generate_report_for_session


def enqueue_report_if_needed(
    *,
    turn_status: str,
    session_id: str,
    store: ReportRepository,
    job_store: ReportJobQueue | None = None,
    job_store_factory: Callable[[], ReportJobQueue] | None = None,
    background_tasks: BackgroundTasks | None,
) -> None:
    if turn_status != "finished":
        return
    if store.get_report_record(session_id) is not None:
        return
    try:
        resolved_job_store = job_store
        if resolved_job_store is None:
            if job_store_factory is None:
                raise RuntimeError("report job store is not configured")
            resolved_job_store = job_store_factory()
        resolved_job_store.enqueue_report_request(session_id)
    except Exception:
        if background_tasks is not None and store.mark_report_processing(session_id):
            background_tasks.add_task(generate_report_for_session, session_id, store)
