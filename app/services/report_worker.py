import logging
import socket
import time
from contextlib import nullcontext
from dataclasses import dataclass

from app.runtime.config import load_worker_runtime_settings
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.report_tasks import execute_report_generation
from app.services.runtime import (
    get_report_executor,
    get_report_job_store,
    get_review_workflow_service,
    get_runtime_signal_store,
)
from app.services.runtime_signal_metrics import CANARY_SIGNAL_CODES
from app.adapters.reliability.runtime_failure import classify_runtime_failure
from app.services.review_workflow import ReportLeaseHeartbeat
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

        with _legacy_lease_context(
            job_store=job_store,
            job=job,
            worker_id=worker_id,
        ) as heartbeat:
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
            except Exception:
                if heartbeat is not None:
                    heartbeat.ensure_owned()
                raise
            if heartbeat is not None:
                heartbeat.ensure_owned()
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
        return _transition_claim(
            job_store,
            "mark_completed",
            job,
            worker_id=worker_id,
        )
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
        return _transition_claim(
            job_store,
            "mark_retryable_failure",
            job,
            str(exc),
            worker_id=worker_id,
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
            return _transition_claim(
                job_store,
                "mark_retryable_failure",
                job,
                str(exc),
                worker_id=worker_id,
                error_code="provider_unavailable",
            )
        return _transition_claim(
            job_store,
            "mark_failed",
            job,
            str(exc),
            worker_id=worker_id,
            error_code="domain_validation_failed",
        )
    except ValueError as exc:
        if job.get("review_engine") == "langgraph-review-v1":
            _record_durable_failure(signal_store, exc)
            return _release_durable_failure(
                job_store, job, worker_id=worker_id
            )
        executor.store.fail_report(job["session_id"], str(exc))
        return _transition_claim(
            job_store,
            "mark_failed",
            job,
            str(exc),
            worker_id=worker_id,
            error_code="domain_validation_failed",
        )
    except Exception as exc:
        if job.get("review_engine") == "langgraph-review-v1":
            _record_durable_failure(signal_store, exc)
            return _release_durable_failure(
                job_store, job, worker_id=worker_id
            )
        executor.store.fail_report(job["session_id"], str(exc))
        return _transition_claim(
            job_store,
            "mark_retryable_failure",
            job,
            str(exc),
            worker_id=worker_id,
            error_code="unexpected_error",
        )


@dataclass
class ReportWorker:
    job_store: object
    executor: object
    worker_id: str
    review_workflow: object | None = None
    signal_store: object | None = None

    def run_one(self):
        return run_one_job(
            job_store=self.job_store,
            executor=self.executor,
            worker_id=self.worker_id,
            review_workflow=self.review_workflow,
            signal_store=self.signal_store,
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
        result = ReportWorker(
            job_store=resolved_job_store,
            executor=resolved_executor,
            worker_id=resolved_worker_id,
            signal_store=signal_store,
        ).run_one()
        if result is None:
            time.sleep(poll_interval_seconds)


def _is_retryable_failure(exc: ReportGenerationFailed) -> bool:
    return str(exc) in RETRYABLE_FAILURE_MESSAGES


def _legacy_lease_context(*, job_store, job: dict, worker_id: str):
    lease_token = job.get("lease_token")
    if not lease_token:
        return nullcontext(None)
    if not all(hasattr(job_store, name) for name in ("assert_lease", "heartbeat")):
        raise ReportLeaseLost("claimed report job cannot publish lease heartbeat")
    return ReportLeaseHeartbeat(
        job_store=job_store,
        job_id=job["job_id"],
        worker_id=worker_id,
        lease_token=lease_token,
        lease_seconds=getattr(job_store, "lease_seconds", 300),
    )


def _transition_claim(
    job_store,
    method_name: str,
    job: dict,
    *args,
    worker_id: str,
    **kwargs,
):
    lease_token = job.get("lease_token")
    if lease_token:
        kwargs.update(worker_id=worker_id, lease_token=lease_token)
    result = getattr(job_store, method_name)(job["job_id"], *args, **kwargs)
    if result is not None:
        return result
    return job_store.get_job(job["job_id"])


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
    configured = load_worker_runtime_settings().report_worker_id
    if configured:
        return configured
    return f"report-worker@{socket.gethostname()}"


if __name__ == "__main__":
    run_forever()
