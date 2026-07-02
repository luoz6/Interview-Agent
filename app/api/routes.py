from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.prep import prepare_interview
from app.services.report_tasks import generate_report_for_session
from app.services.session import InterviewSessionStore


router = APIRouter(prefix="/api")
session_store = InterviewSessionStore()


class PrepRequest(BaseModel):
    job_description: str
    resume_text: str


class AnswerRequest(BaseModel):
    answer: str


def get_session_store() -> InterviewSessionStore:
    return session_store


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
        turn = store.start(plan)
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
    if turn.status == "finished" and store.mark_report_processing(session_id):
        background_tasks.add_task(generate_report_for_session, session_id, store)
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
        return JSONResponse(status_code=202, content={"status": "processing"})
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
