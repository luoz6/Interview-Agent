from celery import Celery

from app.services.config import get_redis_url


celery_app = Celery("interview_agent")
celery_app.conf.update(
    broker_url=get_redis_url(),
    result_backend=get_redis_url(),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
