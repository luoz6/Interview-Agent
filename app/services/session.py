from dataclasses import dataclass, field
from typing import Dict, List, Optional
from uuid import uuid4

from app.services.llm import InterviewLLM
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
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewSession] = {}
        self._llm = llm

    @property
    def llm(self) -> InterviewLLM | None:
        return self._llm

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
            context = self._build_followup_context(session)
            return self._to_turn(
                session,
                follow_up=self._generate_followup(context, current_question.focus),
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

    def _build_followup_context(self, session: InterviewSession) -> list[dict[str, str]]:
        recent_answers = session.answers[-2:]
        context: list[dict[str, str]] = []
        for recorded_answer in recent_answers:
            question = _find_question(session.plan, recorded_answer.question_id)
            if question:
                context.append({"role": "interviewer", "content": question.prompt})
            context.append({"role": "candidate", "content": recorded_answer.answer})
        return context

    def _generate_followup(self, context: list[dict[str, str]], focus: str) -> str:
        try:
            llm = self._llm or _build_default_llm()
            follow_up = llm.generate_followup(context)
            if follow_up:
                return follow_up
        except Exception:
            pass
        return fallback_followup(focus)


def fallback_followup(focus: str) -> str:
    return f"请继续深挖{focus}：你当时做了什么取舍，为什么这样选？"


def _find_question(plan: InterviewPlan, question_id: str) -> InterviewQuestion | None:
    for question in plan.questions:
        if question.id == question_id:
            return question
    return None


def _build_default_llm() -> InterviewLLM:
    from app.services.llm import OpenAIInterviewLLM

    return OpenAIInterviewLLM()
