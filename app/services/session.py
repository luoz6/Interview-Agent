from dataclasses import dataclass, field
from typing import Dict, List, Optional
from uuid import uuid4

from app.services.prep import InterviewPlan, InterviewQuestion


@dataclass(frozen=True)
class RecordedAnswer:
    question_id: str
    answer: str


@dataclass
class InterviewSession:
    session_id: str
    plan: InterviewPlan
    current_index: int = 0
    followup_pending: bool = False
    status: str = "active"
    answers: List[RecordedAnswer] = field(default_factory=list)

    @property
    def current_question(self) -> Optional[InterviewQuestion]:
        if self.current_index >= len(self.plan.questions):
            return None
        return self.plan.questions[self.current_index]


@dataclass(frozen=True)
class InterviewTurn:
    session_id: str
    current_question: Optional[InterviewQuestion]
    follow_up: Optional[str]
    status: str


class InterviewSessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, InterviewSession] = {}

    def start(self, plan: InterviewPlan) -> InterviewTurn:
        session_id = str(uuid4())
        session = InterviewSession(session_id=session_id, plan=plan)
        self._sessions[session_id] = session
        return self._to_turn(session, follow_up=None)

    def get(self, session_id: str) -> InterviewSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("session not found") from exc

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        session = self.get(session_id)
        current_question = session.current_question
        if current_question is None:
            session.status = "finished"
            return self._to_turn(session, follow_up=None)

        session.answers.append(
            RecordedAnswer(question_id=current_question.id, answer=answer.strip())
        )

        if not session.followup_pending:
            session.followup_pending = True
            return self._to_turn(
                session,
                follow_up=f"请继续深挖{current_question.focus}：你当时做了什么取舍，为什么这样选？",
            )

        session.followup_pending = False
        session.current_index += 1
        if session.current_question is None:
            session.status = "finished"
        return self._to_turn(session, follow_up=None)

    def _to_turn(self, session: InterviewSession, follow_up: Optional[str]) -> InterviewTurn:
        return InterviewTurn(
            session_id=session.session_id,
            current_question=session.current_question,
            follow_up=follow_up,
            status=session.status,
        )
