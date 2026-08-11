from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.shared.dependencies import (
    get_session_deletion_service,
    get_session_deletion_worker,
)
from app.api.shared.errors import raise_value_error
from app.runtime.config.memory import load_effective_memory_config


router = APIRouter()


def _require_trusted_local_deletion() -> None:
    if not load_effective_memory_config().privacy.trusted_local_deletion_enabled:
        raise HTTPException(status_code=404, detail="not found")


def _deletion_job_payload(job) -> dict:
    return {
        "deletion_job_id": job.job_id,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "safe_counts": dict(job.safe_counts),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at else None
        ),
    }


@router.delete("/interviews/{session_id}", status_code=202)
def delete_interview_session(
    session_id: str,
    background_tasks: BackgroundTasks,
):
    _require_trusted_local_deletion()
    service = get_session_deletion_service()
    try:
        job = service.request(session_id)
    except ValueError as exc:
        raise_value_error(exc)
    response = _deletion_job_payload(job)
    if job.status in {"queued", "running"}:
        background_tasks.add_task(get_session_deletion_worker().run_once)
    return response


@router.get("/interviews/{session_id}/deletion")
def get_interview_session_deletion(session_id: str):
    _require_trusted_local_deletion()
    try:
        job = get_session_deletion_service().get(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _deletion_job_payload(job)


__all__ = [
    "delete_interview_session",
    "get_interview_session_deletion",
    "router",
]
