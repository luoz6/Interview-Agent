from celery import Celery

from app.runtime.config.compatibility import get_redis_url


celery_app = Celery(
    "interview_agent",
    include=[
        "app.runtime.round_review_tasks",
        "app.runtime.interview_workflow_tasks",
        "app.runtime.principal_memory_tasks",
        "app.runtime.review_workflow_tasks",
    ],
)
celery_app.conf.update(
    broker_url=get_redis_url(),
    result_backend=get_redis_url(),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
