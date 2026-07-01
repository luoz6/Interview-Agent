from dataclasses import asdict

from fastapi import APIRouter, HTTPException
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


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/prep")
def prep_interview(payload: PrepRequest):
    try:
        plan = prepare_interview(payload.job_description, payload.resume_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return asdict(plan)


@router.post("/interviews")
def start_interview(payload: PrepRequest):
    try:
        plan = prepare_interview(payload.job_description, payload.resume_text)
        turn = session_store.start(plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


@router.post("/interviews/{session_id}/answer")
def submit_answer(session_id: str, payload: AnswerRequest):
    try:
        turn = session_store.submit_answer(session_id, payload.answer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


def _turn_to_dict(turn):
    return {
        "session_id": turn.session_id,
        "current_question": asdict(turn.current_question) if turn.current_question else None,
        "follow_up": turn.follow_up,
        "status": turn.status,
    }
