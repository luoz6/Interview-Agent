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
        report_artifact_store=None,
        tombstone_store=None,
        failure_state_store=None,
        failure_state_deployment_scope=None,
        principal_memory_store=None,
        principal_memory_control_store=None,
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
        self.report_artifact_store = report_artifact_store
        self.tombstone_store = tombstone_store
        self.failure_state_store = failure_state_store
        self.failure_state_deployment_scope = failure_state_deployment_scope
        self.principal_memory_store = principal_memory_store
        self.principal_memory_control_store = principal_memory_control_store
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
            "report_history_rows": 0,
            "failure_state_rows": 0,
            "business_sessions": 0,
            "principal_memory_rows": 0,
            "principal_memory_control_rows": 0,
        }
        try:
            report_job = None
            review_job_ids = set()
            if self.report_job_store is not None:
                report_job = self.report_job_store.get_job_by_session(
                    job.session_id
                )
                if report_job is not None and report_job.get("job_id"):
                    review_job_ids.add(str(report_job["job_id"]))
            if self.report_artifact_store is not None:
                list_report_jobs = getattr(
                    self.report_artifact_store,
                    "list_jobs",
                    None,
                )
                if list_report_jobs is not None:
                    review_job_ids.update(
                        str(report_job.job_id)
                        for report_job in list_report_jobs(job.session_id)
                    )
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
                for review_job_id in review_job_ids:
                    counts["artifact_owner_refs"] += (
                        self.context_artifact_store.delete_owner_refs(
                            owner_type="review_job",
                            owner_key=review_job_id,
                        )
                    )
            self._inject("after_artifact_ref_purge", job)
            if self.failure_state_store is not None:
                counts["failure_state_rows"] = (
                    self._delete_failure_state_owner(
                        owner_type="interview_session",
                        owner_key=job.session_id,
                        session_id=job.session_id,
                    )
                )
                for review_job_id in review_job_ids:
                    counts["failure_state_rows"] += (
                        self._delete_failure_state_owner(
                            owner_type="review_job",
                            owner_key=review_job_id,
                            session_id=job.session_id,
                        )
                    )
            self._inject("after_failure_state_purge", job)
            if self.report_artifact_store is not None:
                counts["report_history_rows"] += (
                    self.report_artifact_store.delete_session_history(
                        job.session_id
                    )
                )
            delete_legacy_history = getattr(
                self.report_job_store,
                "delete_session_history",
                None,
            )
            if delete_legacy_history is not None:
                counts["report_history_rows"] += delete_legacy_history(
                    job.session_id
                )
            self._inject("after_report_history_purge", job)
            if self.principal_memory_store is not None:
                counts["principal_memory_rows"] = (
                    self.principal_memory_store.purge_by_session(job.session_id)
                )
            if self.principal_memory_control_store is not None:
                counts["principal_memory_control_rows"] = (
                    self.principal_memory_control_store.purge_session(
                        job.session_id
                    )
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

    def _delete_failure_state_owner(
        self,
        *,
        owner_type,
        owner_key,
        session_id,
    ) -> int:
        if self.failure_state_deployment_scope is None:
            return self.failure_state_store.delete_owner(
                owner_type=owner_type,
                owner_key=owner_key,
            )

        from hashlib import sha256

        from app.services.context_artifact_scope import (
            StableContextArtifactPrivacyScopeResolver,
            privacy_scope_sha256,
        )

        resolver = StableContextArtifactPrivacyScopeResolver()
        if owner_type == "interview_session":
            material = resolver.for_interview(
                deployment_scope=self.failure_state_deployment_scope,
                session_id=session_id,
            )
        elif owner_type == "review_job":
            material = resolver.for_review(
                deployment_scope=self.failure_state_deployment_scope,
                session_id=session_id,
            )
        else:
            raise ValueError("unsupported failure-state deletion owner")
        return self.failure_state_store.delete_owner(
            privacy_scope_sha256=privacy_scope_sha256(material),
            owner_type=owner_type,
            owner_key_sha256=sha256(owner_key.encode("utf-8")).hexdigest(),
        )
