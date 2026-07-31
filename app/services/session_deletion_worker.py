from __future__ import annotations

from time import monotonic

from app.services.memory_metrics import publish_deletion_outcome


class SessionDeletionWorker:
    def __init__(
        self,
        *,
        job_store,
        session_store,
        workflow_service=None,
        question_memory_index=None,
        context_artifact_store=None,
        report_job_store=None,
        tombstone_store=None,
        principal_memory_store=None,
        fault_injector=None,
        worker_id="session-deletion-worker",
        lease_seconds=60,
    ) -> None:
        self.job_store = job_store
        self.session_store = session_store
        self.workflow_service = workflow_service
        self.question_memory_index = question_memory_index
        self.context_artifact_store = context_artifact_store
        self.report_job_store = report_job_store
        self.tombstone_store = tombstone_store
        self.principal_memory_store = principal_memory_store
        self.fault_injector = fault_injector
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def run_once(self):
        job = self.job_store.claim(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        started = monotonic()
        counts = {
            "workflow_rows": 0,
            "question_memory_rows": 0,
            "artifact_owner_refs": 0,
            "business_sessions": 0,
            "principal_memory_rows": 0,
        }
        try:
            if self.workflow_service is not None:
                workflow_counts = self.workflow_service.purge_session(
                    job.session_id
                )
                if isinstance(workflow_counts, dict):
                    counts["workflow_rows"] = sum(workflow_counts.values())
            self._inject("after_workflow_purge", job)
            if self.question_memory_index is not None:
                counts["question_memory_rows"] = (
                    self.question_memory_index.delete_session(job.session_id)
                )
            self._inject("after_question_memory_purge", job)
            if self.context_artifact_store is not None:
                counts["artifact_owner_refs"] = (
                    self.context_artifact_store.delete_owner_refs(
                        owner_type="interview_session",
                        owner_key=job.session_id,
                    )
                )
                if self.report_job_store is not None:
                    report_job = self.report_job_store.get_job_by_session(
                        job.session_id
                    )
                    if report_job is not None and report_job.get("job_id"):
                        counts["artifact_owner_refs"] += (
                            self.context_artifact_store.delete_owner_refs(
                                owner_type="review_job",
                                owner_key=report_job["job_id"],
                            )
                        )
            self._inject("after_artifact_ref_purge", job)
            if self.principal_memory_store is not None:
                counts["principal_memory_rows"] = (
                    self.principal_memory_store.purge_by_session(job.session_id)
                )
            self._inject("after_principal_memory_purge", job)
            counts["business_sessions"] = self.session_store.delete_session(
                job.session_id
            )
            self._inject("after_business_session_purge", job)
            if self.tombstone_store is not None:
                self.tombstone_store.record_completed(job)
            self._inject("after_tombstone_complete", job)
            completed = self.job_store.complete(job, safe_counts=counts)
            publish_deletion_outcome(
                outcome="completed",
                attempts=completed.attempt_count,
                latency_ms=max(0, round((monotonic() - started) * 1000)),
            )
            return completed
        except Exception as exc:
            self.job_store.fail(
                job,
                error_code=type(exc).__name__,
            )
            publish_deletion_outcome(
                outcome="failed",
                attempts=job.attempt_count,
                latency_ms=max(0, round((monotonic() - started) * 1000)),
            )
            raise

    def _inject(self, boundary, job) -> None:
        if self.fault_injector is not None:
            self.fault_injector(boundary, job)
