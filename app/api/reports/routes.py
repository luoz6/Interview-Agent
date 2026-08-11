from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app.api.shared import dependencies
from app.api.shared.errors import raise_if_deleting as _raise_if_deleting
from app.api.shared.errors import raise_value_error as _raise_value_error
from app.api.shared.models import PracticePlanRequest
from app.runtime.config import load_api_runtime_settings
from app.services.practice_plans import PracticePlanError, PracticePlanService
from app.services.report_pdf import ReportPdfRenderer
from app.services.report_reliability import ReportReliabilityProjector
from app.services.session import InterviewSessionStore


router = APIRouter()

# Compatibility dependency exports. Router implementations resolve through the
# shared module so test/runtime replacements are observed at call time.
get_report_job_queue = dependencies.get_report_job_queue
get_report_job_store = dependencies.get_report_job_store
get_session_store = dependencies.get_session_store


@router.get("/reports")
def list_reports(
    status: str | None = None,
    query: str | None = Query(default=None, max_length=200),
    days: int | None = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
):
    if status not in (None, "processing", "completed", "failed"):
        raise HTTPException(status_code=422, detail="invalid status")
    normalized_query = query.strip() if query and query.strip() else None
    reports = store.list_reports(
        status=status,
        query=normalized_query,
        days=days,
        limit=limit,
        offset=offset,
    )
    total = store.count_reports(
        status=status,
        query=normalized_query,
        days=days,
    )
    status_totals = store.report_status_totals(
        query=normalized_query,
        days=days,
    )
    items = [
        _report_summary_to_dict(
            item["session_id"],
            item["record"],
            session_summary=item["session_summary"],
        )
        for item in reports
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_totals": status_totals,
    }
@router.get("/interviews/{session_id}/report")
def get_interview_report(
    session_id: str,
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        return JSONResponse(
            status_code=202,
            content={
                "status": "processing",
                "progress": record.progress.model_dump()
                if record is not None and record.progress is not None
                else None,
            },
        )
    if record.status == "failed":
        raise HTTPException(status_code=500, detail=record.error)
    evaluations = (
        store.list_question_evaluations(session_id)
        if hasattr(store, "list_question_evaluations")
        else []
    )
    reliability = ReportReliabilityProjector().project(
        state,
        record.report,
        evaluations,
        report_path=_public_report_path(record),
    )
    return {
        **record.report.model_dump(),
        "reliability": reliability.model_dump(),
    }


@router.post("/interviews/{session_id}/practice-plan", status_code=201)
def create_practice_plan(
    session_id: str,
    payload: PracticePlanRequest,
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
    plan_store=Depends(dependencies.get_prep_plan_store),
    launch_repository=Depends(dependencies.get_interview_launch_repository),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="interview not found") from exc
    if state["status"] != "finished":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRACTICE_INTERVIEW_NOT_FINISHED",
                "message": "面试结束并生成报告后才能创建针对性练习。",
                "retryable": False,
            },
        )
    record = store.get_report_record(session_id)
    if record is None or record.status != "completed" or record.report is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRACTICE_REPORT_NOT_READY",
                "message": "报告尚未完成，暂时无法创建针对性练习。",
                "retryable": record is not None and record.status == "processing",
            },
        )
    evaluations = (
        store.list_question_evaluations(session_id)
        if hasattr(store, "list_question_evaluations")
        else []
    )
    reliability = ReportReliabilityProjector().project(
        state,
        record.report,
        evaluations,
        report_path=_public_report_path(record),
    )
    try:
        return PracticePlanService(
            prep_plan_store=plan_store,
            launch_repository=launch_repository,
        ).create(
            state=state,
            report=record.report,
            reliability=reliability,
            focus_dimension=payload.focus_dimension,
            session_question_ids=payload.session_question_ids,
        )
    except PracticePlanError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        ) from exc


@router.get("/interviews/{session_id}/report.pdf")
def download_interview_report_pdf(
    session_id: str,
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=409, detail="interview is not finished")

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        raise HTTPException(status_code=409, detail="report is not ready")
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.error)

    pdf_bytes = ReportPdfRenderer().render(record.report)
    filename = f'interview-report-{session_id}.pdf'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{session_id}/report/progress")
def get_interview_report_progress(
    session_id: str,
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")

    record = store.get_report_record(session_id)
    job = _report_job_for_session(session_id)
    detail = _report_progress_detail(
        session_id,
        record,
        job=job,
    )
    if job and job.get("review_engine") == "langgraph-review-v1":
        completed = len(
            [
                item
                for item in store.list_question_evaluations(session_id)
                if item.status == "completed"
            ]
        )
        detail.update(
            {
                "workflow_engine": "langgraph-review-v1",
                "workflow_status": {
                    "queued": "queued",
                    "running": "running",
                    "retrying": "waiting_for_retry",
                    "completed": "completed",
                    "failed": "failed",
                }.get(job.get("status"), "processing"),
                "completed_question_count": completed,
                "total_question_count": len(state["plan"].questions),
                "retrying": job.get("status") == "retrying",
            }
        )
    return detail


@router.post(
    "/interviews/{session_id}/report/requeue",
    status_code=202,
)
def requeue_failed_report(
    session_id: str,
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
    queue=Depends(dependencies.get_report_job_queue),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail="interview session not found",
        ) from exc

    job = queue.get_job_by_session(session_id)
    if job is None:
        record = store.get_report_record(session_id)
        detail = _report_progress_detail(session_id, record, job=None)
        if detail["status"] != "orphaned":
            raise HTTPException(status_code=404, detail="report job not found")
        try:
            created = queue.enqueue_report_request(session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="report queue is unavailable",
            ) from exc
        return {
            "session_id": session_id,
            "report_job_id": str(created["job_id"]),
            "status": "queued",
            "attempt": int(created.get("attempt_count") or 0),
            "recovered_from": "orphaned",
            "report_progress_url": f"/api/interviews/{session_id}/report/progress",
        }

    status = job.get("status")
    if status in {"queued", "retrying", "running"}:
        raise HTTPException(
            status_code=409,
            detail="report job is already queued or processing",
        )
    if status == "completed":
        raise HTTPException(
            status_code=409,
            detail="completed report cannot be requeued",
        )
    if status != "failed":
        raise HTTPException(status_code=409, detail="report job is not failed")

    try:
        store.requeue_report(session_id)
        requeued = queue.requeue_failed(session_id)
    except ValueError as exc:
        store.fail_report(session_id, "report requeue failed")
        raise HTTPException(
            status_code=409,
            detail="report job is not failed",
        ) from exc

    return {
        "session_id": session_id,
        "report_job_id": str(requeued["job_id"]),
        "status": "queued",
        "attempt": int(requeued.get("attempt_count") or 0),
        "recovered_from": "failed",
        "report_progress_url": f"/api/interviews/{session_id}/report/progress",
    }


@router.get("/interviews/{session_id}/question-evaluations")
def get_interview_question_evaluations(
    session_id: str,
    store: InterviewSessionStore = Depends(dependencies.get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        records = store.list_question_evaluations(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    return {
        "session_id": session_id,
        "items": [
            record.model_dump(
                exclude={
                    "evidence_content_sha256",
                    "review_input_sha256",
                    "question_input_sha256",
                    "review_engine",
                    "review_graph_schema_version",
                    "output_sha256",
                }
            )
            for record in records
        ],
        "total": len(records),
    }
def _report_job_id_for_session(session_id: str) -> str | None:
    job = _report_job_for_session(session_id)
    return job.get("job_id") if job else None


def _report_job_for_session(session_id: str) -> dict | None:
    try:
        job = dependencies.get_report_job_store().get_job_by_session(session_id)
    except (AttributeError, RuntimeError):
        return None
    if not job:
        return None
    return job


_PUBLIC_REPORT_PATHS = {
    "microbatch",
    "full_session",
    "full_session_fallback",
}


def _public_report_path(record) -> str | None:
    metadata = record.progress.metadata if record.progress is not None else {}
    value = metadata.get("report_path")
    return value if value in _PUBLIC_REPORT_PATHS else None


def _duration_seconds(summary: dict) -> int | None:
    started_at = summary.get("started_at")
    finished_at = summary.get("finished_at")
    if not started_at or not finished_at:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return max(0, int((finished - started).total_seconds()))


def _report_summary_to_dict(
    session_id: str,
    record,
    *,
    session_summary: dict,
) -> dict:
    report = record.report
    return {
        "session_id": session_id,
        "status": record.status,
        "created_at": record.created_at,
        "finished_at": record.finished_at,
        "overall_score": report.overall_score if report is not None else None,
        "summary": report.summary if report is not None else None,
        "is_fallback": report.is_fallback if report is not None else False,
        "error": record.error,
        "job_title": session_summary.get("job_title"),
        "job_tags": list(session_summary.get("job_tags") or []),
        "question_count": session_summary.get("question_count"),
        "answered_question_count": session_summary.get(
            "answered_question_count", 0
        ),
        "started_at": session_summary.get("started_at"),
        "duration_seconds": _duration_seconds(session_summary),
        "report_path": _public_report_path(record),
        "report_url": f"/api/interviews/{session_id}/report",
        "report_pdf_url": f"/api/interviews/{session_id}/report.pdf"
        if record.status == "completed"
        else None,
    }


def _report_progress_detail(session_id: str, record, *, job: dict | None):
    report_job_id = job.get("job_id") if job else None
    job_health = _report_job_health(job, record)
    if record is None:
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "processing",
            "stage": "queued",
            "percent": 0,
            "message": "Waiting for report generation to start.",
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": {},
            **job_health,
        }

    if record.status == "completed":
        metadata = record.progress.metadata if record.progress is not None else {}
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "completed",
            "stage": "completed",
            "percent": 100,
            "message": "Report completed.",
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": metadata,
            **job_health,
        }

    if record.status == "failed":
        message = record.error or "Report generation failed."
        metadata = record.progress.metadata if record.progress is not None else {}
        error_code = (
            job.get("last_error_code") if job else None
        ) or _public_report_error_code(message)
        retryable = _report_error_retryable(error_code)
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "failed",
            "stage": "failed",
            "percent": 100,
            "message": message,
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": metadata,
            **job_health,
            "retryable": retryable,
            "error": {
                "code": error_code,
                "message": message,
                "retryable": retryable,
            },
        }

    progress = record.progress
    if progress is None:
        stage = "retrieving"
        percent = 0
        message = "Report generation is processing."
        current_question_id = None
    else:
        stage = progress.stage
        percent = progress.percent
        message = progress.message
        current_question_id = progress.current_question_id
    metadata = progress.metadata if progress is not None else {}

    if job_health["orphaned"]:
        return {
            "session_id": session_id,
            "report_job_id": None,
            "status": "orphaned",
            "stage": "orphaned",
            "percent": percent,
            "message": "Report task lost its execution owner.",
            "current_question_id": current_question_id,
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": metadata,
            **job_health,
            "retryable": True,
            "error": {
                "code": "report_job_missing",
                "message": "Report task lost its execution owner.",
                "retryable": True,
            },
        }

    return {
        "session_id": session_id,
        "report_job_id": report_job_id,
        "status": "processing",
        "stage": stage,
        "percent": percent,
        "message": message,
        "current_question_id": current_question_id,
        "events": [],
        "rag": _rag_progress_defaults(),
        "metadata": metadata,
        "error": None,
        **job_health,
    }


def _report_job_health(job: dict | None, record) -> dict:
    threshold = load_api_runtime_settings().report_job_stall_seconds
    now = datetime.now(timezone.utc)
    heartbeat = job.get("heartbeat_at") if job else None
    updated = job.get("updated_at") if job else getattr(record, "created_at", None)
    started = job.get("started_at") if job else None
    lease_expires = job.get("lease_expires_at") if job else None
    activity = heartbeat or updated
    activity_at = _coerce_datetime(activity)
    lease_expires_at = _coerce_datetime(lease_expires)
    age_stalled = bool(
        activity_at is not None and (now - activity_at).total_seconds() > threshold
    )
    lease_stalled = bool(
        job
        and job.get("status") == "running"
        and lease_expires_at is not None
        and lease_expires_at <= now
    )
    orphaned = bool(
        job is None
        and record is not None
        and record.status == "processing"
        and age_stalled
    )
    stalled = orphaned or lease_stalled or bool(
        job and job.get("status") in {"queued", "running", "retrying"} and age_stalled
    )
    return {
        "attempt": int(job.get("attempt_count") or 0) if job else 0,
        "max_attempts": int(job.get("max_attempts") or 0) if job else 0,
        "started_at": _public_datetime(started),
        "last_updated_at": _public_datetime(updated),
        "heartbeat_at": _public_datetime(heartbeat),
        "stalled": stalled,
        "orphaned": orphaned,
        "retryable": bool(job and job.get("status") == "failed"),
    }


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _public_datetime(value) -> str | None:
    parsed = _coerce_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def _public_report_error_code(message: str) -> str:
    normalized = " ".join(message.casefold().split())
    if normalized in {
        "report queue unavailable",
        "report enqueue unavailable",
    }:
        return "report_enqueue_unavailable"
    if "embedding provider is disabled" in normalized:
        return "embedding_provider_disabled"
    if "pgvector" in normalized or "knowledge store" in normalized:
        return "knowledge_store_unavailable"
    if "timeout" in normalized or "timed out" in normalized:
        return "report_provider_timeout"
    return "report_generation_failed"


def _report_error_retryable(error_code: str) -> bool:
    return error_code in {
        "report_enqueue_unavailable",
        "embedding_provider_timeout",
        "knowledge_store_unavailable",
        "report_provider_timeout",
        "provider_unavailable",
        "report_job_missing",
    }


def _rag_progress_defaults() -> dict:
    return {
        "top_k": 5,
        "source_types": ["theory", "expert_benchmark"],
        "matched_chunks": None,
    }

__all__ = [
    "_public_report_error_code",
    "_report_error_retryable",
    "_report_progress_detail",
    "create_practice_plan",
    "download_interview_report_pdf",
    "get_interview_question_evaluations",
    "get_interview_report",
    "get_interview_report_progress",
    "list_reports",
    "requeue_failed_report",
    "router",
]
