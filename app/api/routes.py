import json
from collections.abc import Iterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.services.job_tags import extract_job_tags
from app.services.prep import prepare_interview
from app.services.report_pdf import build_report_pdf
from app.services.report_tasks import generate_report_for_session
from app.services.runtime import get_draft_store, get_report_job_store, get_session_store
from app.services.session import InterviewSessionStore


router = APIRouter(prefix="/api")


class PrepRequest(BaseModel):
    job_description: str
    resume_text: str


class AnswerRequest(BaseModel):
    answer: str


class DraftRequest(BaseModel):
    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    draft_id: str | None = None
    title: str | None = None
    job_tags: list[str] | None = None

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/prep")
def prep_interview(payload: PrepRequest):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = plan.model_dump()
    response["job_tags"] = extract_job_tags(payload.job_description)
    return response


@router.post("/interview-drafts")
def save_interview_draft(payload: DraftRequest, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.save(
            draft_id=payload.draft_id,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            title=payload.title,
            job_tags=payload.job_tags
            if payload.job_tags is not None
            else extract_job_tags(payload.job_description),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/interview-drafts/{draft_id}")
def get_interview_draft(draft_id: str, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.get(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/reports")
def list_reports(
    status: str | None = None,
    limit: int = 20,
    store: InterviewSessionStore = Depends(get_session_store),
):
    if status not in (None, "processing", "completed", "failed"):
        raise HTTPException(status_code=422, detail="invalid status")
    safe_limit = max(1, min(limit, 100))
    reports = store.list_reports(status=status, limit=safe_limit)
    items = [
        _report_summary_to_dict(item["session_id"], item["record"])
        for item in reports
    ]
    return {"items": items, "total": len(items)}


@router.post("/interviews")
def start_interview(
    payload: PrepRequest,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            llm=store.llm,
        )
        job_tags = extract_job_tags(payload.job_description)
        turn = store.start(
            plan,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            job_tags=job_tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


@router.get("/interviews/{session_id}")
def get_interview_session(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        return store.snapshot(session_id)
    except ValueError as exc:
        _raise_value_error(exc)


@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        turn = store.submit_answer(session_id, payload.answer)
    except ValueError as exc:
        _raise_value_error(exc)
    _schedule_report_if_needed(turn.status, session_id, background_tasks, store)
    return _turn_to_dict(turn)


@router.post("/interviews/{session_id}/answer/stream")
def submit_answer_stream(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        prepared = store.prepare_streaming_answer(session_id, payload.answer)
    except ValueError as exc:
        _raise_value_error(exc)

    def event_stream() -> Iterator[str]:
        try:
            if prepared.stream_follow_up:
                chunks: list[str] = []
                for chunk in store.stream_followup(session_id):
                    chunks.append(chunk)
                    yield _sse_event("chunk", {"delta": chunk})
                follow_up_text = "".join(chunks).strip()
            else:
                decision = prepared.state["decision"]
                follow_up_text = decision.get("follow_up") if decision else None

            finalized_state = store.complete_streaming_answer(
                session_id,
                follow_up_text=follow_up_text,
            )
            turn = store._to_turn(finalized_state, follow_up=_extract_follow_up(finalized_state))
            _schedule_report_if_needed(turn.status, session_id, background_tasks, store)
            yield _sse_event("done", _turn_to_dict(turn))
        except Exception as exc:  # pragma: no cover - defensive streaming boundary
            yield _sse_event("error", {"detail": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interviews/{session_id}/finish")
def finish_interview(
    session_id: str,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        turn = store.finish(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    _schedule_report_if_needed(turn.status, session_id, background_tasks, store)
    return _turn_to_dict(turn)


@router.post("/interviews/{session_id}/skip")
def skip_interview_question(
    session_id: str,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        turn = store.skip(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    _schedule_report_if_needed(turn.status, session_id, background_tasks, store)
    return _turn_to_dict(turn)


@router.get("/interviews/{session_id}/report")
def get_interview_report(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
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
    return record.report.model_dump()


@router.get("/interviews/{session_id}/report.pdf")
def download_interview_report_pdf(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=409, detail="interview is not finished")

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        raise HTTPException(status_code=409, detail="report is not ready")
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.error)

    pdf_bytes = build_report_pdf(record.report)
    filename = f'interview-report-{session_id}.pdf'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{session_id}/report/progress")
def get_interview_report_progress(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")

    record = store.get_report_record(session_id)
    return _report_progress_detail(
        session_id,
        record,
        report_job_id=_report_job_id_for_session(session_id),
    )


@router.get("/interviews/{session_id}/question-evaluations")
def get_interview_question_evaluations(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        records = store.list_question_evaluations(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    return {
        "session_id": session_id,
        "items": [record.model_dump() for record in records],
        "total": len(records),
    }


def _turn_to_dict(turn):
    return {
        "session_id": turn.session_id,
        "current_question": turn.current_question.model_dump()
        if turn.current_question
        else None,
        "follow_up": turn.follow_up,
        "status": turn.status,
    }


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _report_job_id_for_session(session_id: str) -> str | None:
    try:
        job = get_report_job_store().get_job_by_session(session_id)
    except (AttributeError, RuntimeError):
        return None
    if not job:
        return None
    return job.get("job_id")


def _report_summary_to_dict(session_id: str, record) -> dict:
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
        "report_url": f"/api/interviews/{session_id}/report",
        "report_pdf_url": f"/api/interviews/{session_id}/report.pdf"
        if record.status == "completed"
        else None,
    }


def _report_progress_detail(session_id: str, record, *, report_job_id: str | None):
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
        }

    if record.status == "completed":
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "completed",
            "stage": "completed",
            "percent": 100,
            "message": "Report completed.",
            "events": [{"stage": "completed", "message": "Report completed."}],
            "rag": _rag_progress_defaults(),
        }

    if record.status == "failed":
        message = record.error or "Report generation failed."
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "failed",
            "stage": "failed",
            "percent": 100,
            "message": message,
            "events": [{"stage": "failed", "message": message}],
            "rag": _rag_progress_defaults(),
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

    return {
        "session_id": session_id,
        "report_job_id": report_job_id,
        "status": "processing",
        "stage": stage,
        "percent": percent,
        "message": message,
        "current_question_id": current_question_id,
        "events": [{"stage": stage, "message": message}],
        "rag": _rag_progress_defaults(),
    }


def _rag_progress_defaults() -> dict:
    return {
        "top_k": 5,
        "source_types": ["theory", "expert_benchmark"],
        "matched_chunks": None,
    }


def _schedule_report_if_needed(
    turn_status: str,
    session_id: str,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore,
) -> None:
    if turn_status != "finished":
        return
    if store.get_report_record(session_id) is not None:
        return
    try:
        get_report_job_store().enqueue_report_request(session_id)
    except RuntimeError:
        if store.mark_report_processing(session_id):
            background_tasks.add_task(generate_report_for_session, session_id, store)


def _extract_follow_up(state) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None


def _raise_value_error(exc: ValueError) -> None:
    detail = str(exc)
    status_code = 404 if detail == "session not found" else 400
    raise HTTPException(status_code=status_code, detail=detail)
