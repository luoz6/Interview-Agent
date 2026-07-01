from app.graphs.interview_state import InterviewState, build_initial_state
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan


class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def start(self, session_id: str, plan: InterviewPlan) -> InterviewState:
        return build_initial_state(session_id=session_id, plan=plan)
