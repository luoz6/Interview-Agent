from app.services.runtime_domain_events import ReviewRetryDueEvent


class ReviewWorkflowConsumer:
    def __init__(self, workflow, job_store) -> None:
        self.workflow = workflow
        self.job_store = job_store

    def consume(self, payload: dict) -> str:
        event = ReviewRetryDueEvent.model_validate(payload)
        job = self.job_store.get_job(event.report_job_id)
        if job is None or job.get("review_engine") != "langgraph-review-v1":
            return "discarded_stale_retry"
        return self.workflow.resume_retry(job, event.next_attempt_number)
