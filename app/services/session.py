from dataclasses import dataclass
from typing import Dict, Optional
from uuid import uuid4

from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState, get_current_question
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion


@dataclass(frozen=True)
class InterviewTurn:
    session_id: str
    current_question: Optional[InterviewQuestion]
    follow_up: Optional[str]
    status: str


class InterviewSessionStore:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewState] = {}
        self._llm = llm
        self._runner = InterviewGraphRunner(llm=llm)

    @property
    def llm(self) -> InterviewLLM | None:
        return self._llm

    def start(self, plan: InterviewPlan) -> InterviewTurn:
        session_id = str(uuid4())
        state = self._runner.start(session_id=session_id, plan=plan)
        self._sessions[session_id] = state
        return self._to_turn(state, follow_up=None)

    def get(self, session_id: str) -> InterviewState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("session not found") from exc

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        state = self.get(session_id)
        new_state = self._runner.submit_answer(state, answer)
        self._sessions[session_id] = new_state
        return self._to_turn(new_state, follow_up=_extract_follow_up(new_state))

    def _to_turn(self, state: InterviewState, follow_up: Optional[str]) -> InterviewTurn:
        return InterviewTurn(
            session_id=state["session_id"],
            current_question=get_current_question(state),
            follow_up=follow_up,
            status=state["status"],
        )


def _extract_follow_up(state: InterviewState) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None
