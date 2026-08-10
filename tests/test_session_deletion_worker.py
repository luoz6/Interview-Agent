from __future__ import annotations

from app.services.session_deletion import InMemorySessionDeletionJobStore
from app.services.session_deletion_worker import SessionDeletionWorker


class ReplaySafeSessionStore:
    def __init__(self):
        self.present = True

    def delete_session(self, session_id):
        if not self.present:
            return 0
        self.present = False
        return 1


class ReportJobs:
    def __init__(self, job_id="review-job-1"):
        self.job_id = job_id

    def get_job_by_session(self, session_id):
        if self.job_id is None:
            return None
        return {"job_id": self.job_id}


class RecordingFailureStateStore:
    def __init__(self):
        self.calls = []
        self.deleted = set()

    def delete_owner(self, *, owner_type, owner_key):
        self.calls.append((owner_type, owner_key))
        identity = (owner_type, owner_key)
        if identity in self.deleted:
            return 0
        self.deleted.add(identity)
        return 1


class FailOnceFailureStateStore(RecordingFailureStateStore):
    def __init__(self):
        super().__init__()
        self.failed = False

    def delete_owner(self, *, owner_type, owner_key):
        if not self.failed:
            self.failed = True
            raise RuntimeError("failure-state deletion unavailable")
        return super().delete_owner(owner_type=owner_type, owner_key=owner_key)


def _queued_job(session_id="PRIVATE_SESSION_CANARY"):
    jobs = InMemorySessionDeletionJobStore(
        job_id_factory=lambda: "delete-failure-state-1"
    )
    jobs.request(session_id)
    return jobs


def test_worker_purges_interview_and_associated_review_failure_states():
    jobs = _queued_job()
    failure_states = RecordingFailureStateStore()
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=ReplaySafeSessionStore(),
        report_job_store=ReportJobs(),
        failure_state_store=failure_states,
    )

    completed = worker.run_once()

    assert failure_states.calls == [
        ("interview_session", "PRIVATE_SESSION_CANARY"),
        ("review_job", "review-job-1"),
    ]
    assert completed.safe_counts["failure_state_rows"] == 2
    assert set(completed.safe_counts) == {
        "workflow_rows",
        "question_memory_rows",
        "artifact_owner_refs",
        "report_history_rows",
        "failure_state_rows",
        "principal_memory_rows",
        "principal_memory_control_rows",
        "business_sessions",
    }
    assert "PRIVATE_SESSION_CANARY" not in repr(completed.safe_counts)
    assert "review-job-1" not in repr(completed.safe_counts)


def test_worker_failure_state_purge_is_replay_safe():
    jobs = _queued_job("session-replay")
    failure_states = RecordingFailureStateStore()
    session_store = ReplaySafeSessionStore()
    first = SessionDeletionWorker(
        job_store=jobs,
        session_store=session_store,
        report_job_store=ReportJobs(),
        failure_state_store=failure_states,
    ).run_once()

    assert first.status == "completed"
    assert first.safe_counts["failure_state_rows"] == 2
    assert SessionDeletionWorker(
        job_store=jobs,
        session_store=session_store,
        report_job_store=ReportJobs(),
        failure_state_store=failure_states,
    ).run_once() is None

    assert failure_states.deleted == {
        ("interview_session", "session-replay"),
        ("review_job", "review-job-1"),
    }


def test_failure_state_purge_failure_is_fenced_and_retryable():
    jobs = _queued_job("session-retry")
    failure_states = FailOnceFailureStateStore()
    session_store = ReplaySafeSessionStore()
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=session_store,
        report_job_store=ReportJobs(),
        failure_state_store=failure_states,
    )

    try:
        worker.run_once()
    except RuntimeError as exc:
        assert str(exc) == "failure-state deletion unavailable"
    else:
        raise AssertionError("failure-state deletion must fail the owned job")
    failed = jobs.get_for_session("session-retry")
    assert failed.status == "failed"
    assert failed.attempt_count == 1

    completed = SessionDeletionWorker(
        job_store=jobs,
        session_store=session_store,
        report_job_store=ReportJobs(),
        failure_state_store=failure_states,
        worker_id="replacement-worker",
    ).run_once()
    assert completed.status == "completed"
    assert completed.attempt_count == 2
    assert completed.safe_counts["failure_state_rows"] == 2


def test_absent_report_job_does_not_broaden_owner_deletion():
    jobs = _queued_job("session-no-review")
    failure_states = RecordingFailureStateStore()

    completed = SessionDeletionWorker(
        job_store=jobs,
        session_store=ReplaySafeSessionStore(),
        report_job_store=ReportJobs(job_id=None),
        failure_state_store=failure_states,
    ).run_once()

    assert failure_states.calls == [
        ("interview_session", "session-no-review")
    ]
    assert completed.safe_counts["failure_state_rows"] == 1


def test_failure_states_are_purged_before_business_session_deletion():
    order = []

    class OrderedQuestionMemory:
        def delete_session(self, session_id):
            order.append("question_memory")
            return 1

    class OrderedArtifactRefs:
        def delete_owner_refs(self, *, owner_type, owner_key):
            order.append(f"artifact:{owner_type}")
            return 1

    class OrderedFailureStates(RecordingFailureStateStore):
        def delete_owner(self, *, owner_type, owner_key):
            order.append(f"failure:{owner_type}")
            return super().delete_owner(
                owner_type=owner_type,
                owner_key=owner_key,
            )

    class OrderedSessionStore(ReplaySafeSessionStore):
        def delete_session(self, session_id):
            order.append("business_session")
            return super().delete_session(session_id)

    completed = SessionDeletionWorker(
        job_store=_queued_job("session-order"),
        session_store=OrderedSessionStore(),
        question_memory_index=OrderedQuestionMemory(),
        context_artifact_store=OrderedArtifactRefs(),
        report_job_store=ReportJobs(),
        failure_state_store=OrderedFailureStates(),
    ).run_once()

    assert completed.status == "completed"
    assert order == [
        "question_memory",
        "artifact:interview_session",
        "artifact:review_job",
        "failure:interview_session",
        "failure:review_job",
        "business_session",
    ]
