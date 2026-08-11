from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Literal

from app.ports.runtime import ReportJobQueue, ReportRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportEnqueueResult:
    status: Literal["not_applicable", "already_exists", "queued", "failed"]
    job_id: str | None = None
    error_code: str | None = None
    retryable: bool = False


def enqueue_report_if_needed(
    *,
    turn_status: str,
    session_id: str,
    store: ReportRepository,
    job_store: ReportJobQueue | None = None,
    job_store_factory: Callable[[], ReportJobQueue] | None = None,
) -> ReportEnqueueResult:
    if turn_status != "finished":
        return ReportEnqueueResult(status="not_applicable")
    if store.get_report_record(session_id) is not None:
        return ReportEnqueueResult(status="already_exists")
    try:
        resolved_job_store = job_store
        if resolved_job_store is None:
            if job_store_factory is None:
                raise RuntimeError("report job store is not configured")
            resolved_job_store = job_store_factory()
        job = resolved_job_store.enqueue_report_request(session_id)
        return ReportEnqueueResult(
            status="queued",
            job_id=str(job["job_id"]) if job.get("job_id") is not None else None,
        )
    except Exception:
        logger.warning(
            "report enqueue failed",
            extra={
                "session_id": session_id,
                "error_code": "report_enqueue_unavailable",
            },
        )
        try:
            store.fail_report(session_id, "report queue unavailable")
        except Exception:
            logger.warning(
                "report enqueue failure projection failed",
                extra={
                    "session_id": session_id,
                    "error_code": "report_enqueue_projection_failed",
                },
            )
        return ReportEnqueueResult(
            status="failed",
            error_code="report_enqueue_unavailable",
            retryable=True,
        )
