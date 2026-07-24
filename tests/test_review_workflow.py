from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.durable_review_graph import DurableReviewGraphDependencies, build_durable_review_graph
from app.services.langgraph_runtime import VersionedGraphRegistry
from app.services.review_workflow import ReviewWorkflowService
from tests.test_durable_review_graph import FakeStore
from tests.test_durable_review_state import make_finished_state, make_job


class SessionStore:
    def get(self, session_id): return make_finished_state()


def test_claimed_job_uses_immutable_graph_version_and_thread():
    store = FakeStore(); committed = []
    graph = build_durable_review_graph(DurableReviewGraphDependencies(
        workflow_store=store,
        review_question=lambda state, question_id: None,
        generate_report=lambda state: {"report_ref": "r", "report_sha256": "d"},
        validate_report=lambda state: "passed",
        commit_report=lambda state: committed.append(state["job_id"]),
    ), checkpointer=InMemorySaver())
    registry = VersionedGraphRegistry(); registry.register("langgraph-review-v1", graph)
    service = ReviewWorkflowService(session_store=SessionStore(), workflow_store=store, graph_registry=registry)

    job = {**make_job(), "session_id": "session-1"}
    result = service.run_claimed_job(job)

    assert result["report_ref"] == "r"
    assert committed == ["job-1"]
