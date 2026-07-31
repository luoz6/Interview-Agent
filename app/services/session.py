from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, Optional
from uuid import uuid4

from app.agents.orchestrator import OrchestratorAgent
from app.graphs.interview_graph import (
    InterviewGraphRunner,
    fallback_followup,
)
from app.graphs.interview_state import (
    InterviewState,
    get_current_question,
    MemoryPolicyVersion,
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
from app.services.agent_runtime import AgentExecutionRunner
from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.report import utc_now_iso as report_utc_now_iso
from app.services.session_errors import SessionVersionConflict
from app.services.memory_retention import (
    InMemorySessionCapacityExceeded,
    InMemorySessionRetentionPolicy,
)


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
        execution_runner: AgentExecutionRunner | None = None,
        retention_policy: InMemorySessionRetentionPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime_event_delivery = "direct"
        self._sessions: Dict[str, InterviewState] = {}
        self._reports: Dict[str, ReportRecord] = {}
        self._question_evaluations: Dict[str, list[QuestionEvaluationRecord]] = {}
        self._llm = llm
        self._retention_policy = (
            retention_policy or InMemorySessionRetentionPolicy()
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._runner = InterviewGraphRunner(
            llm=llm,
            knowledge_binding_resolver=KnowledgeBindingResolver(
                knowledge_repository
            ),
            execution_runner=execution_runner,
        )
        self._orchestrator = OrchestratorAgent(
            llm=llm,
            interview_runner=self._runner,
            execution_runner=execution_runner,
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
        session_id: str | None = None,
        memory_policy_version: MemoryPolicyVersion = "deterministic-v1",
    ) -> InterviewTurn:
        self.cleanup_retention()
        self._ensure_capacity_for_new_session()
        session_id = session_id or str(uuid4())
        state = self._runner.start(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            memory_policy_version=memory_policy_version,
        )
        self._sessions[session_id] = state
        return self._to_turn(state, follow_up=None)

    def cleanup_retention(self) -> int:
        cutoff = self._clock() - timedelta(
            seconds=self._retention_policy.finished_ttl_seconds
        )
        candidates = sorted(
            (
                (self._finished_at(state), session_id)
                for session_id, state in self._sessions.items()
                if state.get("status") == "finished"
                and self._finished_at(state) <= cutoff
            ),
            key=lambda item: item[0],
        )[: self._retention_policy.cleanup_batch_size]
        for _, session_id in candidates:
            self._evict_session(session_id)
        return len(candidates)

    def _ensure_capacity_for_new_session(self) -> None:
        while len(self._sessions) >= self._retention_policy.max_sessions:
            finished = sorted(
                (
                    (self._finished_at(state), session_id)
                    for session_id, state in self._sessions.items()
                    if state.get("status") == "finished"
                ),
                key=lambda item: item[0],
            )
            if not finished:
                raise InMemorySessionCapacityExceeded(
                    "in-memory session capacity is exhausted"
                )
            self._evict_session(finished[0][1])

    def _evict_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._reports.pop(session_id, None)
        self._question_evaluations.pop(session_id, None)

    @staticmethod
    def _finished_at(state: InterviewState) -> datetime:
        raw = (
            state.get("finished_at")
            or state.get("last_checkpoint_at")
            or state.get("started_at")
        )
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def get(self, session_id: str) -> InterviewState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("session not found") from exc

    def mark_deleting(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state.get("deletion_status") == "deleting":
            return False
        state["deletion_status"] = "deleting"
        return True

    def delete_session(self, session_id: str) -> int:
        if session_id not in self._sessions:
            return 0
        self._evict_session(session_id)
        return 1

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
            "workflow_engine": state.get("workflow_engine", "legacy"),
            "graph_schema_version": state.get("graph_schema_version"),
            "memory_policy_version": state["memory_policy_version"],
            "deletion_status": state.get("deletion_status", "active"),
            **interview_assistance_metadata(state),
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
            {"kind": "answer", "answer": answer, "command_id": command_id},
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
        finished_state = self._orchestrator.apply_command(
            state,
            {"kind": "finish", "command_id": command_id},
        )
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
        skipped_state = self._orchestrator.apply_command(
            state,
            {"kind": "skip", "command_id": command_id},
        )
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
            {
                "kind": "prepare_stream",
                "answer": answer,
                "command_id": command_id,
            },
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
                "command_id": command_id,
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
            progress=existing.progress if existing is not None else None,
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
            progress=existing.progress if existing is not None else None,
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
        query: str | None = None,
        days: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = self._filtered_reports(status=status, query=query, days=days)
        return items[offset : offset + limit]

    def count_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
    ) -> int:
        return len(self._filtered_reports(status=status, query=query, days=days))

    def report_status_totals(
        self,
        *,
        query: str | None = None,
        days: int | None = None,
    ) -> dict[str, int]:
        totals = {"all": 0, "processing": 0, "completed": 0, "failed": 0}
        for item in self._filtered_reports(status=None, query=query, days=days):
            report_status = item["record"].status
            totals["all"] += 1
            if report_status in totals:
                totals[report_status] += 1
        return totals

    def _filtered_reports(
        self,
        *,
        status: str | None,
        query: str | None,
        days: int | None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        normalized_query = (query or "").strip().casefold()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
            if days is not None
            else None
        )
        for index, (session_id, record) in enumerate(self._reports.items()):
            if status is not None and record.status != status:
                continue
            state = self._sessions[session_id]
            timestamp = record.finished_at or record.created_at
            if cutoff is not None and _parse_utc_timestamp(timestamp) < cutoff:
                continue
            if normalized_query:
                report_summary = record.report.summary if record.report else ""
                searchable = " ".join(
                    [
                        session_id,
                        state["plan"].title,
                        *state["job_tags"],
                        report_summary,
                        record.status,
                    ]
                ).casefold()
                if normalized_query not in searchable:
                    continue
            items.append(
                {
                    "session_id": session_id,
                    "record": record,
                    "session_summary": {
                        "job_title": state["plan"].title,
                        "job_tags": list(state["job_tags"]),
                        "question_count": len(state["plan"].questions),
                        "started_at": state["started_at"],
                        "finished_at": state["finished_at"],
                    },
                    "_index": index,
                }
            )
        items.sort(
            key=lambda item: (
                item["record"].created_at,
                item["_index"],
                item["session_id"],
            ),
            reverse=True,
        )
        for item in items:
            item.pop("_index", None)
        return items

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


def interview_assistance_metadata(
    state: dict[str, Any],
    *,
    context_route: str | None = None,
    policy_version: str | None = None,
) -> dict[str, Any]:
    route = context_route or state.get("context_route") or "deterministic"
    resolved_policy = (
        policy_version
        or state.get("memory_policy_version")
        or "deterministic-v1"
    )
    assistance_mode = "full"
    user_notice_required = False

    plan = state.get("plan")
    prep_context = getattr(plan, "prep_context", None)
    if getattr(prep_context, "knowledge_status", None) == "degraded":
        assistance_mode = "reduced"

    messages = list(state.get("messages") or [])
    last_interviewer = next(
        (
            message
            for message in reversed(messages)
            if message.get("role") == "interviewer"
        ),
        None,
    )
    if last_interviewer is not None and plan is not None:
        template_followups = {
            fallback_followup(question.focus)
            for question in plan.questions
        }
        if last_interviewer.get("content") in template_followups:
            assistance_mode = "basic"
            user_notice_required = True

    return {
        "context_route": route,
        "assistance_mode": assistance_mode,
        "user_notice_required": user_notice_required,
        "policy_version": resolved_policy,
    }


def _extract_follow_up(state: InterviewState) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
