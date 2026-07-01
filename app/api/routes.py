from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.prep import prepare_interview
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
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        turn = store.submit_answer(session_id, payload.answer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


def _turn_to_dict(turn):
    return {
        "session_id": turn.session_id,
        "current_question": turn.current_question.model_dump()
        if turn.current_question
        else None,
        "follow_up": turn.follow_up,
        "status": turn.status,
    }
