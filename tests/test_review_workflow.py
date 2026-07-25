from langgraph.checkpoint.memory import InMemorySaver
import pytest

from app.graphs.durable_review_graph import DurableReviewGraphDependencies, build_durable_review_graph
from app.services.langgraph_runtime import VersionedGraphRegistry
from app.services.review_workflow import ReviewWorkflowService
from app.services.review_workflow import (
    ReportLeaseHeartbeat,
    ReportLeaseLost,
)
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


class LeaseStore:
    def __init__(self, owned=True):
        self.owned = owned
        self.assertions = []
        self.heartbeats = []

    def assert_lease(self, job_id, *, worker_id, lease_token):
        self.assertions.append((job_id, worker_id, lease_token))
        return self.owned

    def heartbeat(
        self,
        job_id,
        *,
        worker_id,
        lease_token,
        lease_seconds,
    ):
        self.heartbeats.append(
            (job_id, worker_id, lease_token, lease_seconds)
        )
        return self.owned


def test_report_lease_heartbeat_rejects_invalid_initial_owner():
    heartbeat = ReportLeaseHeartbeat(
        job_store=LeaseStore(owned=False),
        job_id="job-1",
        worker_id="worker-1",
        lease_token="token-1",
        lease_seconds=30,
    )

    with pytest.raises(ReportLeaseLost):
        heartbeat.__enter__()


def test_report_lease_heartbeat_stops_cleanly():
    store = LeaseStore()
    heartbeat = ReportLeaseHeartbeat(
        job_store=store,
        job_id="job-1",
        worker_id="worker-1",
        lease_token="token-1",
        lease_seconds=30,
    )

    with heartbeat:
        assert heartbeat._thread is not None
        assert heartbeat._thread.is_alive()

    assert heartbeat._thread is not None
    assert not heartbeat._thread.is_alive()
    assert store.assertions == [("job-1", "worker-1", "token-1")]
