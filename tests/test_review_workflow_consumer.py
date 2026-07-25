from app.services.review_workflow_consumer import ReviewWorkflowConsumer
from app.services.runtime_domain_events import ReviewRetryDueEvent


class FakeJobStore:
    def __init__(self, job):
        self.job = job

    def get_job(self, job_id):
        if self.job is None or self.job["job_id"] != job_id:
            return None
        return dict(self.job)


class FakeWorkflow:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or ["completed"])
        self.calls = []

    def resume_retry(self, job, attempt):
        self.calls.append((dict(job), attempt))
        return self.outcomes.pop(0)


def payload(job_id="job-1", attempt=2):
    return ReviewRetryDueEvent(
        event_id=f"retry-{job_id}-{attempt}",
        session_id="session-1",
        report_job_id=job_id,
        next_attempt_number=attempt,
    ).model_dump(mode="json")


def durable_job():
    return {
        "job_id": "job-1",
        "session_id": "session-1",
        "review_engine": "langgraph-review-v1",
        "review_graph_schema_version": "langgraph-review-v1",
    }


def test_missing_job_retry_is_discarded():
    workflow = FakeWorkflow()
    consumer = ReviewWorkflowConsumer(workflow, FakeJobStore(None))

    assert consumer.consume(payload()) == "discarded_stale_retry"
    assert workflow.calls == []


def test_legacy_job_retry_is_discarded():
    job = durable_job() | {"review_engine": "legacy"}
    workflow = FakeWorkflow()
    consumer = ReviewWorkflowConsumer(workflow, FakeJobStore(job))

    assert consumer.consume(payload()) == "discarded_stale_retry"
    assert workflow.calls == []


def test_matching_retry_resumes_once():
    workflow = FakeWorkflow(["completed"])
    consumer = ReviewWorkflowConsumer(workflow, FakeJobStore(durable_job()))

    assert consumer.consume(payload()) == "completed"
    assert len(workflow.calls) == 1
    assert workflow.calls[0][1] == 2


def test_duplicate_retry_does_not_complete_twice():
    workflow = FakeWorkflow(["completed", "discarded_stale_retry"])
    consumer = ReviewWorkflowConsumer(workflow, FakeJobStore(durable_job()))

    assert consumer.consume(payload()) == "completed"
    assert consumer.consume(payload()) == "discarded_stale_retry"
    assert len(workflow.calls) == 2
