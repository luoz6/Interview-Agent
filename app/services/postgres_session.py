from copy import deepcopy
from uuid import uuid4

from app.adapters.postgres.message_repository import PostgresMessageRepository
from app.adapters.postgres.question_evaluation_repository import (
    PostgresQuestionEvaluationRepository,
)
from app.adapters.postgres.report_repository import PostgresReportRepository
from app.adapters.postgres.session_repository import PostgresSessionRepository
from app.adapters.postgres.store_schema_adapter import (
    PostgresSessionSchemaAdapter,
)
from app.adapters.postgres.unit_of_work import PostgresUnitOfWork
from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.agent_runtime import AgentExecutionRunner
from app.services.interview_rounds import round_closed_event_from_transition
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.prep import InterviewPlan
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.report import utc_now_iso as report_utc_now_iso
from app.domain.interview.models import InterviewTurn, PreparedInterviewTurn
from app.domain.interview.state_machine import (
    advance_state_metadata as _advance_state_metadata,
    already_finalized_streaming_answer as _already_finalized_streaming_answer,
    ensure_expected_version as _ensure_expected_version,
    extract_follow_up as _extract_follow_up,
    is_duplicate_command as _is_duplicate_command,
    should_stream_follow_up as _should_stream_follow_up,
)
from app.services.session_plan_binding import SessionPlanBinding
from app.services.session import InterviewSessionStore
from app.services.runtime_domain_events import RoundClosedEvent


class PostgresInterviewSessionStore(InterviewSessionStore):
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        agent_run_connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        llm: InterviewLLM | None = None,
        knowledge_repository=None,
        execution_runner: AgentExecutionRunner | None = None,
        schema_mode: str | None = None,
    ) -> None:
        super().__init__(
            llm=llm,
            knowledge_repository=knowledge_repository,
            execution_runner=execution_runner,
        )
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.messages_table = f"{table_prefix}_messages"
        self.reports_table = f"{table_prefix}_reports"
        self.question_evaluations_table = f"{table_prefix}_question_evaluations"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            PostgresSessionSchemaAdapter(
                self._connection_provider,
                table_prefix=self.table_prefix,
                sessions_table=self.sessions_table,
                messages_table=self.messages_table,
                reports_table=self.reports_table,
                question_evaluations_table=self.question_evaluations_table,
            ).ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (
                    self.sessions_table,
                    self.messages_table,
                    self.reports_table,
                    self.question_evaluations_table,
                    f"{table_prefix}_schema_migrations",
                ),
            )
        self._message_repository = PostgresMessageRepository(
            connection_provider,
            messages_table=self.messages_table,
        )
        self._report_repository = PostgresReportRepository(
            connection_provider,
            sessions_table=self.sessions_table,
            messages_table=self.messages_table,
            reports_table=self.reports_table,
        )
        self._question_evaluation_repository = (
            PostgresQuestionEvaluationRepository(
                connection_provider,
                question_evaluations_table=self.question_evaluations_table,
            )
        )
        self._runtime_control = PostgresRuntimeControlStore(
            dsn=dsn,
            connection_provider=connection_provider,
            agent_run_connection_provider=agent_run_connection_provider,
            table_prefix=table_prefix,
            schema_mode=self.schema_mode,
        )
        self._session_repository = PostgresSessionRepository(
            connection_provider,
            sessions_table=self.sessions_table,
            message_repository=self._message_repository,
            runtime_outbox_repository=self._runtime_control,
        )
        self.runtime_event_delivery = "transactional_outbox"

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self._connection_provider)

    def list_runtime_tables(self) -> list[str]:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = ANY(%s)
                    ORDER BY table_name
                    """,
                    (
                        [
                            self.sessions_table,
                            self.messages_table,
                            self.reports_table,
                            self.question_evaluations_table,
                        ],
                    ),
                )
                return [row[0] for row in cursor.fetchall()]

    def list_messages(self, session_id: str) -> list[dict]:
        return self._message_repository.list_messages(session_id)

    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        session_id: str | None = None,
        memory_policy_version: str = "deterministic-v1",
        plan_binding: SessionPlanBinding | None = None,
    ) -> InterviewTurn:
        session_id = session_id or str(uuid4())
        state = self._runner.start(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            memory_policy_version=memory_policy_version,
            plan_binding=plan_binding,
        )
        self._insert_state(state)
        return self._to_turn(state, follow_up=None)

    def insert_durable_session_shell(
        self,
        *,
        session_id: str,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        graph_version: str = "langgraph-v1",
        memory_policy_version: str = "deterministic-v1",
        plan_binding: SessionPlanBinding | None = None,
    ) -> None:
        from app.graphs.interview_state import build_initial_state

        state = build_initial_state(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            memory_policy_version=memory_policy_version,
            plan_binding=plan_binding,
        )
        if graph_version not in {"langgraph-v1", "langgraph-v2"}:
            raise ValueError("unsupported durable interview graph version")
        state["workflow_engine"] = graph_version
        state["graph_schema_version"] = graph_version
        state["messages"] = []
        state["state_version"] = 0
        state["checkpoint_version"] = 0
        state["projection_sha256"] = None
        self._insert_state(state)

    def insert_session_in_transaction(
        self,
        cursor,
        *,
        session_id: str,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        graph_version: str = "legacy",
        memory_policy_version: str = "deterministic-v1",
    ) -> InterviewState:
        """Insert a complete session shell with a caller-owned cursor.

        This method deliberately does not acquire a connection and never
        commits or rolls back. It is the only session insertion entry used by
        the cross-store launch coordinator.
        """
        if graph_version in {"langgraph-v1", "langgraph-v2"}:
            from app.graphs.interview_state import build_initial_state

            state = build_initial_state(
                session_id=session_id,
                plan=plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                memory_policy_version=memory_policy_version,
            )
            state["workflow_engine"] = graph_version
            state["graph_schema_version"] = graph_version
            state["messages"] = []
            state["state_version"] = 0
            state["checkpoint_version"] = 0
            state["projection_sha256"] = None
        elif graph_version == "legacy":
            state = self._runner.start(
                session_id=session_id,
                plan=plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                memory_policy_version=memory_policy_version,
            )
        else:
            raise ValueError("unsupported interview graph version")
        self._insert_state_with_cursor(cursor, state)
        return state

    def get(self, session_id: str) -> InterviewState:
        return self._session_repository.get(session_id)

    def mark_deleting(self, session_id: str) -> bool:
        with self.unit_of_work() as unit_of_work:
            changed = self._session_repository.mark_deleting(
                unit_of_work.cursor,
                session_id,
            )
            unit_of_work.commit()
        return changed

    def delete_session(self, session_id: str) -> int:
        with self.unit_of_work() as unit_of_work:
            count = self._session_repository.delete_session(
                unit_of_work.cursor,
                session_id,
            )
            unit_of_work.commit()
        return count

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
        before_state = deepcopy(state)
        previous_version = state["state_version"]
        new_state = self._orchestrator.apply_command(
            state,
            {"kind": "answer", "answer": answer, "command_id": command_id},
        )
        new_state = _advance_state_metadata(new_state, command_id=command_id)
        event = round_closed_event_from_transition(before_state, new_state)
        self._replace_state(
            new_state,
            expected_previous_version=previous_version,
            outbox_event=event,
        )
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
        before_state = deepcopy(state)
        previous_version = state["state_version"]
        finished_state = self._orchestrator.apply_command(
            state,
            {"kind": "finish", "command_id": command_id},
        )
        finished_state = _advance_state_metadata(
            finished_state,
            command_id=command_id,
        )
        event = round_closed_event_from_transition(
            before_state,
            finished_state,
        )
        self._replace_state(
            finished_state,
            expected_previous_version=previous_version,
            outbox_event=event,
        )
        return self._to_turn(
            finished_state,
            follow_up=_extract_follow_up(finished_state),
        )

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
        before_state = deepcopy(state)
        previous_version = state["state_version"]
        skipped_state = self._orchestrator.apply_command(
            state,
            {"kind": "skip", "command_id": command_id},
        )
        skipped_state = _advance_state_metadata(
            skipped_state,
            command_id=command_id,
        )
        event = round_closed_event_from_transition(
            before_state,
            skipped_state,
        )
        self._replace_state(
            skipped_state,
            expected_previous_version=previous_version,
            outbox_event=event,
        )
        return self._to_turn(
            skipped_state,
            follow_up=_extract_follow_up(skipped_state),
        )

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
        previous_version = state["state_version"]
        prepared_state = self._orchestrator.apply_command(
            state,
            {
                "kind": "prepare_stream",
                "answer": answer,
                "command_id": command_id,
            },
        )
        prepared_state = _advance_state_metadata(
            prepared_state,
            command_id=command_id,
        )
        self._replace_state(prepared_state, expected_previous_version=previous_version)
        return PreparedInterviewTurn(
            state=prepared_state,
            stream_follow_up=_should_stream_follow_up(prepared_state),
        )

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
        before_state = deepcopy(prepared_state)
        previous_version = prepared_state["state_version"]
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
        event = round_closed_event_from_transition(
            before_state,
            finalized_state,
        )
        self._replace_state(
            finalized_state,
            expected_previous_version=previous_version,
            outbox_event=event,
        )
        return finalized_state

    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if self.get_report_record(session_id) is not None:
            return False
        previous_version = state["state_version"]
        state["phase"] = "review"
        state["phase_status"] = "active"
        state["review_status"] = "processing"
        state = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
        self._replace_state(state, expected_previous_version=previous_version)
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="processing",
                progress=ReportProgress(
                    stage="retrieving",
                    percent=20,
                    message="Retrieving role-specific knowledge references.",
                ),
            ),
        )
        return True

    def update_report_progress(
        self,
        session_id: str,
        progress: ReportProgress,
    ) -> None:
        record = self.get_report_record(session_id)
        if record is None:
            raise ValueError("report record not found")
        if record.status != "processing":
            raise ValueError("report is not processing")
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="processing",
                progress=progress,
                created_at=record.created_at,
                finished_at=record.finished_at,
            ),
        )

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        state = self.get(session_id)
        existing = self.get_report_record(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        previous_version = state["state_version"]
        state["phase"] = "review"
        state["phase_status"] = "completed"
        state["review_status"] = "completed"
        state = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
        self._replace_state(state, expected_previous_version=previous_version)
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="completed",
                progress=existing.progress if existing is not None else None,
                report=report,
                created_at=created_at,
                finished_at=report_utc_now_iso(),
            ),
        )

    def fail_report(self, session_id: str, error: str) -> None:
        state = self.get(session_id)
        existing = self.get_report_record(session_id)
        if existing is not None and existing.status in {"completed", "failed"}:
            return
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        previous_version = state["state_version"]
        state["phase"] = "review"
        state["phase_status"] = "failed"
        state["review_status"] = "failed"
        state = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
        self._replace_state(state, expected_previous_version=previous_version)
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="failed",
                progress=existing.progress if existing is not None else None,
                error=error,
                created_at=created_at,
                finished_at=report_utc_now_iso(),
            ),
        )

    def requeue_report(self, session_id: str) -> None:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        existing = self.get_report_record(session_id)
        if existing is not None and existing.status == "completed":
            raise ValueError("completed report cannot be requeued")
        previous_version = state["state_version"]
        state["phase"] = "review"
        state["phase_status"] = "active"
        state["review_status"] = "processing"
        state = _advance_state_metadata(
            state,
            command_id=None,
            record_command_id=False,
        )
        self._replace_state(state, expected_previous_version=previous_version)
        if existing is None or existing.status != "processing":
            self._upsert_report_record(
                session_id,
                ReportRecord(
                    status="processing",
                    progress=ReportProgress(
                        stage="retrieving",
                        percent=0,
                        message="Waiting for report worker to start.",
                    ),
                    created_at=(
                        existing.created_at
                        if existing is not None
                        else report_utc_now_iso()
                    ),
                ),
            )

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        record = self._report_repository.get_report_record(session_id)
        if record is None:
            self.get(session_id)
        return record

    def list_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        return self._report_repository.list_reports(
            status=status,
            query=query,
            days=days,
            limit=limit,
            offset=offset,
        )

    def count_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
    ) -> int:
        return self._report_repository.count_reports(
            status=status,
            query=query,
            days=days,
        )

    def report_status_totals(
        self,
        *,
        query: str | None = None,
        days: int | None = None,
    ) -> dict[str, int]:
        return self._report_repository.report_status_totals(
            query=query,
            days=days,
        )

    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        with self.unit_of_work() as unit_of_work:
            for record in records:
                self._question_evaluation_repository.upsert_question_evaluation(
                    unit_of_work.cursor,
                    record,
                )
            unit_of_work.commit()

    def upsert_question_evaluation(
        self,
        session_id: str,
        record: QuestionEvaluationRecord,
    ) -> None:
        self.get(session_id)
        with self.unit_of_work() as unit_of_work:
            self._question_evaluation_repository.upsert_question_evaluation(
                unit_of_work.cursor,
                record,
            )
            unit_of_work.commit()

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        self.get(session_id)
        return self._question_evaluation_repository.list_question_evaluations(
            session_id
        )


    def _insert_state(self, state: InterviewState) -> None:
        with self.unit_of_work() as unit_of_work:
            self._session_repository.insert_state(unit_of_work.cursor, state)
            unit_of_work.commit()

    def _insert_state_with_cursor(self, cursor, state: InterviewState) -> None:
        self._session_repository.insert_state(cursor, state)

    def _replace_state(
        self,
        state: InterviewState,
        *,
        expected_previous_version: int | None = None,
        outbox_event: RoundClosedEvent | None = None,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            self._session_repository.replace_state(
                unit_of_work.cursor,
                state,
                expected_previous_version=expected_previous_version,
                outbox_event=outbox_event,
            )
            unit_of_work.commit()

    def _upsert_report_record(self, session_id: str, record: ReportRecord) -> None:
        with self.unit_of_work() as unit_of_work:
            self._report_repository.upsert_report_record(
                unit_of_work.cursor,
                session_id,
                record,
            )
            unit_of_work.commit()
