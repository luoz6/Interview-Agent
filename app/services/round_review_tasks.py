from app.services.celery_app import celery_app
from app.services.runtime_event_consumer import (
    consume_round_review_event_payload,
)


@celery_app.task(
    bind=True,
    name="app.services.round_review_tasks.run_closed_round_review",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def run_closed_round_review(self, payload: dict) -> None:
    worker_id = self.request.id or payload.get("event_id", "direct")
    outcome = consume_round_review_event_payload(
        payload,
        worker_id=worker_id,
    )
    if outcome.status == "reschedule":
        raise self.retry(
            countdown=outcome.countdown_seconds,
            exc=RuntimeError(
                outcome.error_code or "runtime_work_retry"
            ),
        )
