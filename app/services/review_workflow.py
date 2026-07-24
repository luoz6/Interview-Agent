from __future__ import annotations

from app.graphs.durable_review_state import make_durable_review_initial_state, review_thread_id
from langgraph.types import Command


class ReviewWorkflowService:
    def __init__(self, *, session_store, workflow_store, graph_registry, checkpointer_runtime=None) -> None:
        self.session_store = session_store
        self.workflow_store = workflow_store
        self.graph_registry = graph_registry
        self.checkpointer_runtime = checkpointer_runtime

    def graph_for_job(self, job: dict):
        version = job.get("review_graph_schema_version")
        if job.get("review_engine") != "langgraph-review-v1" or not version:
            raise ValueError("report job is not a durable review job")
        return self.graph_registry.get(version)

    def run_claimed_job(self, job: dict):
        graph = self.graph_for_job(job)
        config = {"configurable": {"thread_id": review_thread_id(job["job_id"])}}
        snapshot = graph.get_state(config)
        if snapshot.values:
            return graph.invoke(None, config=config)
        state = self.session_store.get(job["session_id"])
        return graph.invoke(make_durable_review_initial_state(job, state), config=config)

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
