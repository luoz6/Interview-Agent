from __future__ import annotations

from contextlib import nullcontext
from threading import Event, Thread

from app.graphs.durable_review_state import make_durable_review_initial_state, review_thread_id
from langgraph.types import Command


class ReportLeaseLost(RuntimeError):
    pass


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
        self.interval_seconds = max(0.1, lease_seconds / 3)
        self._stop = Event()
        self._lost = Event()
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
        if self._lost.is_set() or not self.job_store.assert_lease(
            self.job_id,
            worker_id=self.worker_id,
            lease_token=self.lease_token,
        ):
            self._lost.set()
            raise ReportLeaseLost("report job lease is no longer owned")

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            if not self.job_store.heartbeat(
                self.job_id,
                worker_id=self.worker_id,
                lease_token=self.lease_token,
                lease_seconds=self.lease_seconds,
            ):
                self._lost.set()
                return


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
    ) -> None:
        self.session_store = session_store
        self.workflow_store = workflow_store
        self.graph_registry = graph_registry
        self.checkpointer_runtime = checkpointer_runtime
        self.job_store = job_store
        self.lease_seconds = lease_seconds
        self.heartbeat_factory = heartbeat_factory

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
            snapshot = graph.get_state(config)
            if snapshot.values:
                result = graph.invoke(None, config=config)
            else:
                state = make_durable_review_initial_state(
                    job, self.session_store.get(job["session_id"])
                )
                self.workflow_store.initialize_run(
                    job_id=state["job_id"],
                    session_id=state["session_id"],
                    graph_schema_version=state[
                        "review_graph_schema_version"
                    ],
                    input_sha256=state["review_input_manifest"][
                        "input_sha256"
                    ],
                )
                result = graph.invoke(state, config=config)
            return result

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
        graph = self.graph_for_job(job)
        config = {"configurable": {"thread_id": review_thread_id(job["job_id"])}}
        snapshot = graph.get_state(config)
        if snapshot.next != ("wait_for_retry",):
            return "discarded_stale_retry"
        if snapshot.values.get("expected_retry_attempt") != next_attempt_number:
            return "discarded_stale_retry"
        graph.invoke(Command(resume={"next_attempt_number": next_attempt_number}), config=config)
        return "completed"

    def purge_job(self, job_id: str) -> None:
        if self.checkpointer_runtime is not None:
            self.checkpointer_runtime.delete_thread(review_thread_id(job_id))
