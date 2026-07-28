from langgraph.checkpoint.memory import InMemorySaver
import pytest
from threading import Event

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


def test_report_lease_heartbeat_exception_fails_closed_with_original_cause():
    failure = RuntimeError("renewal unavailable")

    class RaisingHeartbeatStore(LeaseStore):
        def __init__(self):
            super().__init__()
            self.called = Event()

        def heartbeat(self, *args, **kwargs):
            self.called.set()
            raise failure

    store = RaisingHeartbeatStore()
    heartbeat = ReportLeaseHeartbeat(
        job_store=store,
        job_id="job-1",
        worker_id="worker-1",
        lease_token="token-1",
        lease_seconds=30,
    )
    heartbeat.interval_seconds = 0.01

    with heartbeat:
        assert store.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(ReportLeaseLost) as caught:
            heartbeat.ensure_owned()

    assert caught.value.__cause__ is failure
    assert heartbeat._thread is not None
    assert not heartbeat._thread.is_alive()


def test_report_lease_assertion_exception_is_normalized():
    failure = RuntimeError("assertion unavailable")

    class RaisingAssertionStore(LeaseStore):
        def assert_lease(self, *args, **kwargs):
            raise failure

    heartbeat = ReportLeaseHeartbeat(
        job_store=RaisingAssertionStore(),
        job_id="job-1",
        worker_id="worker-1",
        lease_token="token-1",
        lease_seconds=30,
    )

    with pytest.raises(ReportLeaseLost) as caught:
        heartbeat.__enter__()

    assert caught.value.__cause__ is failure


def test_report_lease_rechecks_background_loss_after_synchronous_assertion():
    failure = RuntimeError("renewal unavailable")

    class RacingAssertionStore(LeaseStore):
        heartbeat = None

        def assert_lease(self, *args, **kwargs):
            assert self.heartbeat is not None
            self.heartbeat._mark_lost(failure)
            return True

    store = RacingAssertionStore()
    heartbeat = ReportLeaseHeartbeat(
        job_store=store,
        job_id="job-1",
        worker_id="worker-1",
        lease_token="token-1",
        lease_seconds=30,
    )
    store.heartbeat = heartbeat

    with pytest.raises(ReportLeaseLost) as caught:
        heartbeat.ensure_owned()

    assert caught.value.__cause__ is failure
