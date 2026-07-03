from dataclasses import dataclass
from typing import Dict, Iterator, Optional
from uuid import uuid4

from app.graphs.interview_graph import InterviewGraphRunner, fallback_followup
from app.graphs.interview_state import InterviewState, get_current_question
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport, ReportProgress, ReportRecord


@dataclass(frozen=True)
class InterviewTurn:
    session_id: str
    current_question: Optional[InterviewQuestion]
    follow_up: Optional[str]
    status: str


@dataclass(frozen=True)
class PreparedInterviewTurn:
    state: InterviewState
    stream_follow_up: bool


class InterviewSessionStore:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewState] = {}
        self._reports: Dict[str, ReportRecord] = {}
        self._llm = llm
        self._runner = InterviewGraphRunner(llm=llm)

    @property
    def llm(self) -> InterviewLLM | None:
        return self._llm

    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewTurn:
        session_id = str(uuid4())
        state = self._runner.start(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )
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

    def prepare_streaming_answer(self, session_id: str, answer: str) -> PreparedInterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        state = self.get(session_id)
        prepared_state = self._runner.prepare_answer(state, answer)
        decision = prepared_state["decision"]
        should_stream = bool(decision and decision["action"] == "follow_up")
        self._sessions[session_id] = prepared_state
        return PreparedInterviewTurn(state=prepared_state, stream_follow_up=should_stream)

    def complete_streaming_answer(
        self,
        session_id: str,
        *,
        follow_up_text: str | None = None,
    ) -> InterviewState:
        prepared_state = self.get(session_id)
        finalized_state = self._runner.finalize_prepared_answer(
            prepared_state,
            follow_up=follow_up_text,
        )
        self._sessions[session_id] = finalized_state
        return finalized_state

    def stream_followup(self, session_id: str) -> Iterator[str]:
        state = self.get(session_id)
        decision = state["decision"]
        question = get_current_question(state)
        fallback_text = decision.get("follow_up") if decision else None
        if not fallback_text and question is not None:
            fallback_text = fallback_followup(question.focus)
        try:
            llm = self._llm
            if llm is None:
                from app.services.llm import OpenAIInterviewLLM

                llm = OpenAIInterviewLLM()
            emitted = False
            for chunk in llm.stream_followup(_build_followup_context(state)):
                if not chunk:
                    continue
                emitted = True
                yield chunk
        except Exception:
            if fallback_text:
                yield fallback_text
            return

        if not emitted and fallback_text:
            yield fallback_text

    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if session_id in self._reports:
            return False
        self._reports[session_id] = ReportRecord(
            status="processing",
            progress=ReportProgress(
                stage="retrieving",
                percent=20,
                message="Retrieving role-specific knowledge references.",
            ),
        )
        return True

    def update_report_progress(
        self,
        session_id: str,
        progress: ReportProgress,
    ) -> None:
        self.get(session_id)
        record = self._reports.get(session_id)
        if record is None:
            raise ValueError("report record not found")
        if record.status != "processing":
            raise ValueError("report is not processing")
        self._reports[session_id] = ReportRecord(
            status="processing",
            progress=progress,
        )

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        self.get(session_id)
        self._reports[session_id] = ReportRecord(status="completed", report=report)

    def fail_report(self, session_id: str, error: str) -> None:
        self.get(session_id)
        self._reports[session_id] = ReportRecord(status="failed", error=error)

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        self.get(session_id)
        return self._reports.get(session_id)

    def _to_turn(self, state: InterviewState, follow_up: Optional[str]) -> InterviewTurn:
        current_question = None if state["status"] == "finished" else get_current_question(state)
        return InterviewTurn(
            session_id=state["session_id"],
            current_question=current_question,
            follow_up=follow_up,
            status="finished" if state["status"] == "finished" else "active",
        )


def _extract_follow_up(state: InterviewState) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None


def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
