from app.services.celery_app import celery_app


@celery_app.task(
    name="app.services.review_workflow_tasks.run_review_workflow_event"
)
def run_review_workflow_event(payload: dict) -> dict:
    from app.services.runtime import get_review_workflow_consumer

    status = get_review_workflow_consumer().consume(payload)
    return {"status": status}
