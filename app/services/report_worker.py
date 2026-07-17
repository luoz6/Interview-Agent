import logging
import os
import socket
import time

from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.report_tasks import execute_report_generation
from app.services.runtime import get_report_executor, get_report_job_store


logger = logging.getLogger(__name__)


RETRYABLE_FAILURE_MESSAGES = {
    "pgvector knowledge store is unavailable",
}


def run_one_job(
    *,
    job_store,
    executor,
    worker_id: str,
):
    job_store.repair_orphan_processing_reports()
    job = job_store.claim_next(worker_id=worker_id)
    if job is None:
        return None

    try:
        report = execute_report_generation(
            session_id=job["session_id"],
            store=executor.store,
            llm=executor.llm,
            vector_store=executor.vector_store,
            execution_runner=getattr(
                executor,
                "execution_runner",
                None,
            ),
            attempt_number=max(
                1,
                int(job.get("attempt_count", 0)) + 1,
            ),
        )
        assert report is not None
        if report.is_fallback:
            logger.warning(
                "Report job completed with fallback report",
                extra={"job_id": job["job_id"], "session_id": job["session_id"]},
            )
        else:
            logger.info(
                "Report job completed with grounded report",
                extra={"job_id": job["job_id"], "session_id": job["session_id"]},
            )
        return job_store.mark_completed(job["job_id"])
    except ReportGenerationTimeout as exc:
        executor.store.fail_report(job["session_id"], str(exc))
        return job_store.mark_retryable_failure(job["job_id"], str(exc))
    except ReportGenerationFailed as exc:
        executor.store.fail_report(job["session_id"], str(exc))
        if _is_retryable_failure(exc):
            return job_store.mark_retryable_failure(job["job_id"], str(exc))
        return job_store.mark_failed(job["job_id"], str(exc))
    except ValueError as exc:
        executor.store.fail_report(job["session_id"], str(exc))
        return job_store.mark_failed(job["job_id"], str(exc))
    except Exception as exc:
        executor.store.fail_report(job["session_id"], str(exc))
        return job_store.mark_retryable_failure(job["job_id"], str(exc))


def run_forever(
    *,
    worker_id: str | None = None,
    poll_interval_seconds: float = 1.0,
    job_store=None,
    executor=None,
) -> None:
    resolved_executor = executor or get_report_executor()
    resolved_job_store = job_store or get_report_job_store()
    resolved_worker_id = worker_id or _default_worker_id()
    while True:
        result = run_one_job(
            job_store=resolved_job_store,
            executor=resolved_executor,
            worker_id=resolved_worker_id,
        )
        if result is None:
            time.sleep(poll_interval_seconds)


def _is_retryable_failure(exc: ReportGenerationFailed) -> bool:
    return str(exc) in RETRYABLE_FAILURE_MESSAGES


def _default_worker_id() -> str:
    configured = os.getenv("REPORT_WORKER_ID")
    if configured:
        return configured
    return f"report-worker@{socket.gethostname()}"


if __name__ == "__main__":
    run_forever()
