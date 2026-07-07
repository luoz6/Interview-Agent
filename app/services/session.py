from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional
from uuid import uuid4

from app.graphs.interview_graph import (
    INTERVIEW_FINISHED_MESSAGE,
    InterviewGraphRunner,
    fallback_followup,
)
from app.graphs.interview_state import (
    InterviewState,
    count_candidate_answers_for_question,
    get_current_question,
    utc_now_iso,
)
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.report import utc_now_iso as report_utc_now_iso


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

    def snapshot(self, session_id: str) -> dict[str, Any]:
        state = self.get(session_id)
        _ensure_state_metadata(state)
        current_question = None if state["status"] == "finished" else get_current_question(state)
        questions = [
            {
                **question.model_dump(),
                "state": _question_state(state, index),
            }
            for index, question in enumerate(state["plan"].questions)
        ]
        answer_counts = _question_answer_counts(state)
        return {
            "session_id": state["session_id"],
            "status": state["status"],
            "current_index": state["current_index"],
            "total_questions": len(state["plan"].questions),
            "completed_questions": answer_counts["answered"] + answer_counts["skipped"],
            "answered_questions": answer_counts["answered"],
            "skipped_questions": answer_counts["skipped"],
            "unanswered_questions": answer_counts["unanswered"],
            "started_at": state["started_at"],
            "finished_at": state["finished_at"],
            "elapsed_seconds": _elapsed_seconds(state),
            "estimated_remaining_seconds": answer_counts["pending_or_current"] * 6 * 60,
            "job_tags": list(state["job_tags"]),
            "current_question": current_question.model_dump() if current_question else None,
            "questions": questions,
            "messages": [
                {
                    "role": message["role"],
                    "content": message["content"],
                    "question_id": message["question_id"],
                }
                for message in state["messages"]
            ],
        }

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        state = self.get(session_id)
        new_state = self._runner.submit_answer(state, answer)
        self._sessions[session_id] = new_state
        return self._to_turn(new_state, follow_up=_extract_follow_up(new_state))

    def finish(self, session_id: str) -> InterviewTurn:
        state = self.get(session_id)
        finished_state = finish_interview_state(state)
        self._sessions[session_id] = finished_state
        return self._to_turn(finished_state, follow_up=_extract_follow_up(finished_state))

    def skip(self, session_id: str) -> InterviewTurn:
        state = self.get(session_id)
        skipped_state = skip_interview_question_state(state)
        self._sessions[session_id] = skipped_state
        return self._to_turn(skipped_state, follow_up=_extract_follow_up(skipped_state))

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
        emitted = False
        for chunk in self._runner.stream_followup(state):
            emitted = True
            yield chunk
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
            created_at=record.created_at,
            finished_at=record.finished_at,
        )

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        self.get(session_id)
        existing = self._reports.get(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        self._reports[session_id] = ReportRecord(
            status="completed",
            report=report,
            created_at=created_at,
            finished_at=report_utc_now_iso(),
        )

    def fail_report(self, session_id: str, error: str) -> None:
        self.get(session_id)
        existing = self._reports.get(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        self._reports[session_id] = ReportRecord(
            status="failed",
            error=error,
            created_at=created_at,
            finished_at=report_utc_now_iso(),
        )

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        self.get(session_id)
        return self._reports.get(session_id)

    def list_reports(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for index, (session_id, record) in enumerate(self._reports.items()):
            if status is not None and record.status != status:
                continue
            items.append({"session_id": session_id, "record": record, "_index": index})
        items.sort(
            key=lambda item: (item["record"].created_at, item["_index"]),
            reverse=True,
        )
        for item in items:
            item.pop("_index", None)
        return items[:limit]

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


def _ensure_state_metadata(state: InterviewState) -> None:
    state.setdefault("skipped_question_ids", [])
    state.setdefault("started_at", utc_now_iso())
    state.setdefault("finished_at", None)


def _parse_state_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _elapsed_seconds(state: InterviewState) -> int:
    started = _parse_state_timestamp(state.get("started_at"))
    if started is None:
        return 0
    finished = _parse_state_timestamp(state.get("finished_at")) or datetime.now(timezone.utc)
    return max(0, int((finished - started).total_seconds()))


def _record_skip_if_unanswered(state: InterviewState) -> None:
    _ensure_state_metadata(state)
    question = get_current_question(state)
    if question is None:
        return
    if count_candidate_answers_for_question(state, question.id) > 0:
        return
    if question.id not in state["skipped_question_ids"]:
        state["skipped_question_ids"].append(question.id)


def finish_interview_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    state["current_index"] = len(state["plan"].questions)
    state["decision"] = {
        "action": "finish",
        "follow_up": None,
        "reason": "user_finished_interview",
    }
    state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
    state["status"] = "finished"
    if not _has_terminal_message(state):
        state["messages"].append(
            {
                "role": "interviewer",
                "content": INTERVIEW_FINISHED_MESSAGE,
                "question_id": None,
            }
        )
    state["finished_at"] = state["finished_at"] or utc_now_iso()
    return state


def skip_interview_question_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    _record_skip_if_unanswered(state)
    next_index = state["current_index"] + 1
    if next_index >= len(state["plan"].questions):
        return finish_interview_state(state)

    next_question = state["plan"].questions[next_index]
    state["current_index"] = next_index
    state["decision"] = {
        "action": "next_question",
        "follow_up": None,
        "reason": "user_skipped_question",
    }
    state["pending_output"] = next_question.prompt
    state["messages"].append(
        {
            "role": "interviewer",
            "content": next_question.prompt,
            "question_id": next_question.id,
        }
    )
    return state


def _question_answer_counts(state: InterviewState) -> dict[str, int]:
    counts = {"answered": 0, "skipped": 0, "unanswered": 0, "pending_or_current": 0}
    for index, _ in enumerate(state["plan"].questions):
        question_state = _question_state(state, index)
        if question_state in ("answered", "skipped", "unanswered"):
            counts[question_state] += 1
        else:
            counts["unanswered"] += 1
            counts["pending_or_current"] += 1
    return counts


def _question_state(state: InterviewState, index: int) -> str:
    _ensure_state_metadata(state)
    question = state["plan"].questions[index]
    if question.id in state["skipped_question_ids"]:
        return "skipped"
    if count_candidate_answers_for_question(state, question.id) > 0:
        return "answered"
    if state["status"] == "finished":
        return "unanswered"
    if index == state["current_index"]:
        return "current"
    return "pending"


def _has_terminal_message(state: InterviewState) -> bool:
    return bool(
        state["messages"]
        and state["messages"][-1]["role"] == "interviewer"
        and state["messages"][-1]["content"] == INTERVIEW_FINISHED_MESSAGE
        and state["messages"][-1]["question_id"] is None
    )


def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
