from __future__ import annotations

from contextlib import nullcontext
from threading import Event, Lock, Thread

from app.graphs.durable_review_state import make_durable_review_initial_state, review_thread_id
from langgraph.types import Command
from app.services.review_execution import bind_review_execution_lease
from app.services.workflow_thread_lock import (
    NoopWorkflowThreadLock,
    ReportLeaseLost,
    WorkflowThreadBusy,
    review_thread_identity,
)

class ReportLeaseHeartbeat:
    def __init__(
        self,
        *,
        job_store,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> None:
        self.job_store = job_store
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.1, min(10.0, lease_seconds / 3))
        self._stop = Event()
        self._lost = Event()
        self._failure_lock = Lock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def __enter__(self):
        self.ensure_owned()
        self._thread = Thread(
            target=self._run,
            name=f"report-lease-{self.job_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            self._raise_lost()
        try:
            owned = self.job_store.assert_lease(
                self.job_id,
                worker_id=self.worker_id,
                lease_token=self.lease_token,
            )
        except Exception as exc:
            self._mark_lost(exc)
            self._raise_lost(
                "report job lease ownership could not be verified"
            )
        if not owned:
            self._mark_lost()
            self._raise_lost()
        if self._lost.is_set():
            self._raise_lost()

    def _mark_lost(self, failure: Exception | None = None) -> None:
        with self._failure_lock:
            if self._lost.is_set():
                return
            self._failure = failure
            self._lost.set()

    def _raise_lost(
        self, message: str = "report job lease is no longer owned"
    ) -> None:
        error = ReportLeaseLost(message)
        with self._failure_lock:
            failure = self._failure
        if failure is not None:
            raise error from failure
        raise error

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval_seconds):
                if not self.job_store.heartbeat(
                    self.job_id,
                    worker_id=self.worker_id,
                    lease_token=self.lease_token,
                    lease_seconds=self.lease_seconds,
                ):
                    self._mark_lost()
                    return
        except Exception as exc:
            self._mark_lost(exc)


class ReviewWorkflowService:
    def __init__(
        self,
        *,
        session_store,
        workflow_store,
        graph_registry,
        checkpointer_runtime=None,
        job_store=None,
        lease_seconds: int = 300,
        heartbeat_factory=ReportLeaseHeartbeat,
        thread_lock=None,
    ) -> None:
        self.session_store = session_store
        self.workflow_store = workflow_store
        self.graph_registry = graph_registry
        self.checkpointer_runtime = checkpointer_runtime
        self.job_store = job_store
        self.lease_seconds = lease_seconds
        self.heartbeat_factory = heartbeat_factory
        self.thread_lock = thread_lock or NoopWorkflowThreadLock()

    def graph_for_job(self, job: dict):
        version = job.get("review_graph_schema_version")
        if job.get("review_engine") != "langgraph-review-v1" or not version:
            raise ValueError("report job is not a durable review job")
        return self.graph_registry.get(version)

    def run_claimed_job(self, job: dict, *, worker_id: str | None = None):
        graph = self.graph_for_job(job)
        config = {"configurable": {"thread_id": review_thread_id(job["job_id"])}}
        lease = self._lease_context(job, worker_id)
        with lease as heartbeat:
            execution_lease = (
                bind_review_execution_lease(
                    job_id=job["job_id"],
                    worker_id=worker_id,
                    lease_token=job["lease_token"],
                )
                if self.job_store is not None
                else nullcontext(None)
            )
            with execution_lease:
                return self._run_under_execution_authority(
                    job=job,
                    worker_id=worker_id,
                    graph=graph,
                    config=config,
                    heartbeat=heartbeat,
                )

    def _run_under_execution_authority(
        self, *, job, worker_id, graph, config, heartbeat
    ):
        try:
            with self.thread_lock.hold(
                review_thread_identity(job["job_id"]),
                workflow_type="review",
            ) as ownership:
                snapshot = graph.get_state(config)
                if snapshot.next == ("wait_for_retry",):
                    expected = snapshot.values.get(
                        "expected_retry_attempt"
                    )
                    scheduled = job.get("scheduled_attempt")
                    if expected != scheduled:
                        raise ValueError(
                            "claimed review retry does not match graph cursor"
                        )
                    result = graph.invoke(
                        Command(
                            resume={
                                "next_attempt_number": expected,
                            }
                        ),
                        config=config,
                    )
                elif snapshot.values:
                    result = graph.invoke(None, config=config)
                else:
                    state = make_durable_review_initial_state(
                        job, self.session_store.get(job["session_id"])
                    )
                    result = graph.invoke(state, config=config)
                post_snapshot = graph.get_state(config)
                # A terminal graph commit deliberately clears the Report Job
                # lease inside the same fenced SQL transaction. Non-terminal
                # returns (especially interrupts) must still prove ownership.
                if heartbeat is not None and post_snapshot.next:
                    heartbeat.ensure_owned()
                if ownership is not None:
                    ownership.ensure_owned()
                return result
        except WorkflowThreadBusy:
            if (
                self.job_store is not None
                and worker_id
                and job.get("lease_token")
            ):
                self.job_store.release_claim_for_retry(
                    job["job_id"],
                    worker_id=worker_id,
                    lease_token=job["lease_token"],
                )
            return {"status": "workflow_thread_busy"}

    def _lease_context(self, job: dict, worker_id: str | None):
        if self.job_store is None:
            return nullcontext(None)
        lease_token = job.get("lease_token")
        if not worker_id or not lease_token:
            raise ReportLeaseLost("claimed durable review job has no lease token")
        return self.heartbeat_factory(
            job_store=self.job_store,
            job_id=job["job_id"],
            worker_id=worker_id,
            lease_token=lease_token,
            lease_seconds=self.lease_seconds,
        )

    def snapshot(self, job: dict) -> dict:
        graph = self.graph_for_job(job)
        state = graph.get_state({"configurable": {"thread_id": review_thread_id(job["job_id"])}})
        values = state.values or {}
        return {
            "workflow_engine": job["review_engine"],
            "workflow_status": state.next[0] if state.next else "completed",
            "completed_question_count": len(values.get("completed_question_ids", [])),
            "total_question_count": len(values.get("review_input_manifest", {}).get("questions", [])),
            "quality_repair_count": values.get("quality_repair_count", 0),
        }

    def resume_retry(self, job: dict, next_attempt_number: int):
        if self.job_store is None:
            raise RuntimeError("review retry scheduling requires a Report Job store")
        return self.job_store.schedule_review_retry(
            job["job_id"], next_attempt_number=next_attempt_number
        )

    def purge_job(self, job_id: str) -> None:
        if self.checkpointer_runtime is not None:
            self.checkpointer_runtime.delete_thread(review_thread_id(job_id))
