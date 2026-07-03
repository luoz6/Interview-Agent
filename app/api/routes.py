import json
from collections.abc import Iterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.services.job_tags import extract_job_tags
from app.services.prep import prepare_interview
from app.services.report_tasks import generate_report_for_session
from app.services.runtime import get_report_job_store, get_session_store
from app.services.session import InterviewSessionStore


router = APIRouter(prefix="/api")


class PrepRequest(BaseModel):
    job_description: str
    resume_text: str


class AnswerRequest(BaseModel):
    answer: str


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/prep")
def prep_interview(
    payload: PrepRequest,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            llm=store.llm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return plan.model_dump()


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
        raise HTTPException(status_code=400, detail=str(exc))
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
        raise HTTPException(status_code=400, detail=str(exc))

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


def _schedule_report_if_needed(
    turn_status: str,
    session_id: str,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore,
) -> None:
    if turn_status != "finished":
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
