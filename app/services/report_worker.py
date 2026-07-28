import logging
import os
import socket
import time

from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.report_tasks import execute_report_generation
from app.services.runtime import (
    get_report_executor,
    get_report_job_store,
    get_review_workflow_service,
    get_runtime_signal_store,
)
from app.services.runtime_signal_metrics import CANARY_SIGNAL_CODES
from app.services.runtime_work import classify_runtime_failure
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    ReportLeaseLost,
    WorkflowThreadLockLost,
)


logger = logging.getLogger(__name__)


RETRYABLE_FAILURE_MESSAGES = {
    "pgvector knowledge store is unavailable",
}


def _record_durable_outcome(signal_store, outcome) -> None:
    if not isinstance(outcome, dict):
        return
    code = outcome.get("error_code") or outcome.get("status")
    _write_runtime_signal(signal_store, code)


def _record_durable_failure(signal_store, exc: Exception) -> None:
    _write_runtime_signal(
        signal_store,
        classify_runtime_failure(exc).code,
    )


def _write_runtime_signal(signal_store, code) -> None:
    if signal_store is None or code not in CANARY_SIGNAL_CODES:
        return
    try:
        signal_store.increment(
            workflow_type="review",
            signal_code=code,
        )
    except Exception:
        logger.warning(
            "runtime canary signal write failed",
            extra={"error_code": "canary_signal_write_failed"},
        )


def run_one_job(
    *,
    job_store,
    executor,
    worker_id: str,
    review_workflow=None,
    signal_store=None,
):
    job_store.repair_orphan_processing_reports()
    job = job_store.claim_next(worker_id=worker_id)
    if job is None:
        return None

    try:
        if job.get("review_engine") == "langgraph-review-v1":
            workflow = review_workflow or get_review_workflow_service()
            outcome = workflow.run_claimed_job(job, worker_id=worker_id)
            _record_durable_outcome(signal_store, outcome)
            return job_store.get_job(job["job_id"])

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
    except (ReportLeaseLost, FencedWriteRejected, WorkflowThreadLockLost) as exc:
        # Ownership failures belong to the replacement owner. The stale worker
        # must not fail the public report or reschedule the newly claimed job.
        _record_durable_failure(signal_store, exc)
        return job_store.get_job(job["job_id"])
    except ReportGenerationTimeout as exc:
        if job.get("review_engine") == "langgraph-review-v1":
            _record_durable_failure(signal_store, exc)
            return _release_durable_failure(
                job_store, job, worker_id=worker_id
            )
        executor.store.fail_report(job["session_id"], str(exc))
        return job_store.mark_retryable_failure(
            job["job_id"],
            str(exc),
            error_code="provider_timeout",
        )
    except ReportGenerationFailed as exc:
        if job.get("review_engine") == "langgraph-review-v1":
            _record_durable_failure(signal_store, exc)
            return _release_durable_failure(
                job_store, job, worker_id=worker_id
            )
        executor.store.fail_report(job["session_id"], str(exc))
        if _is_retryable_failure(exc):
            return job_store.mark_retryable_failure(
                job["job_id"],
                str(exc),
                error_code="provider_unavailable",
            )
        return job_store.mark_failed(
            job["job_id"],
            str(exc),
            error_code="domain_validation_failed",
        )
    except ValueError as exc:
        if job.get("review_engine") == "langgraph-review-v1":
            _record_durable_failure(signal_store, exc)
            return _release_durable_failure(
                job_store, job, worker_id=worker_id
            )
        executor.store.fail_report(job["session_id"], str(exc))
        return job_store.mark_failed(
            job["job_id"],
            str(exc),
            error_code="domain_validation_failed",
        )
    except Exception as exc:
        if job.get("review_engine") == "langgraph-review-v1":
            _record_durable_failure(signal_store, exc)
            return _release_durable_failure(
                job_store, job, worker_id=worker_id
            )
        executor.store.fail_report(job["session_id"], str(exc))
        return job_store.mark_retryable_failure(
            job["job_id"],
            str(exc),
            error_code="unexpected_error",
        )


def run_forever(
    *,
    worker_id: str | None = None,
    poll_interval_seconds: float = 1.0,
    job_store=None,
    executor=None,
) -> None:
    resolved_executor = executor or get_report_executor()
    resolved_job_store = job_store or get_report_job_store()
    signal_store = get_runtime_signal_store()
    resolved_worker_id = worker_id or _default_worker_id()
    while True:
        result = run_one_job(
            job_store=resolved_job_store,
            executor=resolved_executor,
            worker_id=resolved_worker_id,
            signal_store=signal_store,
        )
        if result is None:
            time.sleep(poll_interval_seconds)


def _is_retryable_failure(exc: ReportGenerationFailed) -> bool:
    return str(exc) in RETRYABLE_FAILURE_MESSAGES


def _release_durable_failure(job_store, job: dict, *, worker_id: str):
    lease_token = job.get("lease_token")
    release = getattr(job_store, "release_claim_for_retry", None)
    if lease_token and release is not None:
        release(
            job["job_id"],
            worker_id=worker_id,
            lease_token=lease_token,
        )
    return job_store.get_job(job["job_id"])


def _default_worker_id() -> str:
    configured = os.getenv("REPORT_WORKER_ID")
    if configured:
        return configured
    return f"report-worker@{socket.gethostname()}"


if __name__ == "__main__":
    run_forever()
