from app.services.celery_app import celery_app
from app.services.round_review_runner import run_round_review_event_payload


@celery_app.task(name="app.services.round_review_tasks.run_closed_round_review")
def run_closed_round_review(payload: dict) -> None:
    run_round_review_event_payload(payload)
