from dataclasses import dataclass
from typing import Any, Protocol

from app.graphs.interview_state import InterviewState


class InterviewQuestionView(Protocol):
    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class InterviewTurn:
    session_id: str
    current_question: InterviewQuestionView | None
    follow_up: str | None
    status: str


@dataclass(frozen=True)
class PreparedInterviewTurn:
    state: InterviewState
    stream_follow_up: bool


__all__ = [
    "InterviewQuestionView",
    "InterviewTurn",
    "PreparedInterviewTurn",
]
