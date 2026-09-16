from app.runtime.celery_app import celery_app


@celery_app.task(
    name="app.services.interview_workflow_tasks.run_interview_workflow_event"
)
def run_interview_workflow_event(payload: dict) -> dict:
    from app.runtime.composition import get_interview_workflow_consumer

    outcome = get_interview_workflow_consumer().consume(payload)
    return {"status": outcome.status}
