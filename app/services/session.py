from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional
from uuid import uuid4

from app.agents.orchestrator import OrchestratorAgent
from app.graphs.interview_graph import (
    InterviewGraphRunner,
    fallback_followup,
)
from app.graphs.interview_state import (
    InterviewState,
    get_current_question,
    utc_now_iso,
)
from app.graphs.interview_transitions import (
    _elapsed_seconds,
    _ensure_state_metadata,
    _question_answer_counts,
    _question_state,
    finish_interview_state,
    skip_interview_question_state,
)
from app.services.llm import InterviewLLM
from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.report import utc_now_iso as report_utc_now_iso
from app.services.session_errors import SessionVersionConflict


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
    def __init__(
        self,
        llm: InterviewLLM | None = None,
        knowledge_repository=None,
    ) -> None:
        self._sessions: Dict[str, InterviewState] = {}
        self._reports: Dict[str, ReportRecord] = {}
        self._question_evaluations: Dict[str, list[QuestionEvaluationRecord]] = {}
        self._llm = llm
        self._runner = InterviewGraphRunner(
            llm=llm,
            knowledge_binding_resolver=KnowledgeBindingResolver(
                knowledge_repository
            ),
        )
        self._orchestrator = OrchestratorAgent(
            llm=llm,
            interview_runner=self._runner,
        )

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
            "phase": state["phase"],
            "phase_status": state["phase_status"],
            "review_status": state["review_status"],
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
            "state_version": state["state_version"],
            "checkpoint_version": state["checkpoint_version"],
            "last_checkpoint_at": state["last_checkpoint_at"],
            "last_command_id": state["last_command_id"],
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

    def submit_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        state = self.get(session_id)
        if _is_duplicate_command(state, command_id):
            return self._to_turn(state, follow_up=_extract_follow_up(state))
        _ensure_expected_version(state, expected_version)
        new_state = self._orchestrator.apply_command(
            state,
            {"kind": "answer", "answer": answer},
        )
        new_state = _advance_state_metadata(new_state, command_id=command_id)
        self._sessions[session_id] = new_state
        return self._to_turn(new_state, follow_up=_extract_follow_up(new_state))

    def finish(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        state = self.get(session_id)
        if _is_duplicate_command(state, command_id):
            return self._to_turn(state, follow_up=_extract_follow_up(state))
        _ensure_expected_version(state, expected_version)
        finished_state = self._orchestrator.apply_command(state, {"kind": "finish"})
        finished_state = _advance_state_metadata(finished_state, command_id=command_id)
        self._sessions[session_id] = finished_state
        return self._to_turn(finished_state, follow_up=_extract_follow_up(finished_state))

    def skip(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        state = self.get(session_id)
        if _is_duplicate_command(state, command_id):
            return self._to_turn(state, follow_up=_extract_follow_up(state))
        _ensure_expected_version(state, expected_version)
        skipped_state = self._orchestrator.apply_command(state, {"kind": "skip"})
        skipped_state = _advance_state_metadata(skipped_state, command_id=command_id)
        self._sessions[session_id] = skipped_state
        return self._to_turn(skipped_state, follow_up=_extract_follow_up(skipped_state))

    def prepare_streaming_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> PreparedInterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        state = self.get(session_id)
        if _is_duplicate_command(state, command_id):
            return PreparedInterviewTurn(
                state=state,
                stream_follow_up=_should_stream_follow_up(state),
            )
        _ensure_expected_version(state, expected_version)
        prepared_state = self._orchestrator.apply_command(
            state,
            {"kind": "prepare_stream", "answer": answer},
        )
        prepared_state = _advance_state_metadata(prepared_state, command_id=command_id)
        should_stream = _should_stream_follow_up(prepared_state)
        self._sessions[session_id] = prepared_state
        return PreparedInterviewTurn(state=prepared_state, stream_follow_up=should_stream)

    def complete_streaming_answer(
        self,
        session_id: str,
        *,
        follow_up_text: str | None = None,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewState:
        prepared_state = self.get(session_id)
        if _already_finalized_streaming_answer(prepared_state):
            return prepared_state
        _ensure_expected_version(prepared_state, expected_version)
        finalized_state = self._orchestrator.apply_command(
            prepared_state,
            {
                "kind": "complete_stream",
                "follow_up_text": follow_up_text,
            },
        )
        finalized_state = _advance_state_metadata(
            finalized_state,
            command_id=command_id,
            record_command_id=False,
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
        state["phase"] = "review"
        state["phase_status"] = "active"
        state["review_status"] = "processing"
        self._sessions[session_id] = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
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
        state = self.get(session_id)
        existing = self._reports.get(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        state["phase"] = "review"
        state["phase_status"] = "completed"
        state["review_status"] = "completed"
        self._sessions[session_id] = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
        self._reports[session_id] = ReportRecord(
            status="completed",
            report=report,
            created_at=created_at,
            finished_at=report_utc_now_iso(),
        )

    def fail_report(self, session_id: str, error: str) -> None:
        state = self.get(session_id)
        existing = self._reports.get(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        state["phase"] = "review"
        state["phase_status"] = "failed"
        state["review_status"] = "failed"
        self._sessions[session_id] = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
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

    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        existing_records = self._question_evaluations.get(session_id, [])
        self._question_evaluations[session_id] = _merge_question_evaluation_records(
            existing_records,
            records,
        )

    def upsert_question_evaluation(
        self,
        session_id: str,
        record: QuestionEvaluationRecord,
    ) -> None:
        self.get(session_id)
        existing_records = self._question_evaluations.get(session_id, [])
        self._question_evaluations[session_id] = _merge_question_evaluation_records(
            existing_records,
            [record],
        )

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        self.get(session_id)
        return list(self._question_evaluations.get(session_id, []))

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


def _ensure_expected_version(
    state: InterviewState,
    expected_version: int | None,
) -> None:
    if expected_version is None:
        return
    if expected_version != state["state_version"]:
        raise SessionVersionConflict(
            expected_version=expected_version,
            actual_version=state["state_version"],
        )


def _is_duplicate_command(state: InterviewState, command_id: str | None) -> bool:
    return bool(command_id and state.get("last_command_id") == command_id)


def _advance_state_metadata(
    state: InterviewState,
    *,
    command_id: str | None,
    record_command_id: bool = True,
) -> InterviewState:
    state["state_version"] += 1
    # Local V1 stores checkpoints inline, so checkpoint_version mirrors
    # state_version until an external checkpoint store exists.
    state["checkpoint_version"] = state["state_version"]
    state["last_checkpoint_at"] = utc_now_iso()
    if record_command_id:
        state["last_command_id"] = command_id
    return state


def _already_completed_streaming_followup(
    state: InterviewState,
    follow_up_text: str | None,
) -> bool:
    if not follow_up_text or not state["messages"]:
        return False
    last = state["messages"][-1]
    return last["role"] == "interviewer" and last["content"] == follow_up_text


def _already_finalized_streaming_answer(state: InterviewState) -> bool:
    if not state["messages"]:
        return False
    if state["messages"][-1]["role"] != "interviewer":
        return False
    return state["decision"] is not None


def _should_stream_follow_up(state: InterviewState) -> bool:
    decision = state["decision"]
    if decision is None or decision["action"] != "follow_up":
        return False
    return not _already_finalized_streaming_answer(state)


def _merge_question_evaluation_records(
    existing_records: list[QuestionEvaluationRecord],
    new_records: list[QuestionEvaluationRecord],
) -> list[QuestionEvaluationRecord]:
    merged_by_question_id: dict[str, QuestionEvaluationRecord] = {}
    ordered_question_ids: list[str] = []

    for record in existing_records:
        if record.question_id not in merged_by_question_id:
            ordered_question_ids.append(record.question_id)
        merged_by_question_id[record.question_id] = record

    for record in new_records:
        existing = merged_by_question_id.get(record.question_id)
        if existing is not None:
            record = record.model_copy(update={"created_at": existing.created_at})
        elif record.question_id not in merged_by_question_id:
            ordered_question_ids.append(record.question_id)
        merged_by_question_id[record.question_id] = record

    return [merged_by_question_id[question_id] for question_id in ordered_question_ids]
