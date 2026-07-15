import json
from uuid import uuid4

from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.report import utc_now_iso as report_utc_now_iso
from app.services.session import (
    InterviewSessionStore,
    InterviewTurn,
    PreparedInterviewTurn,
    _advance_state_metadata,
    _already_finalized_streaming_answer,
    _ensure_expected_version,
    _extract_follow_up,
    _is_duplicate_command,
    _should_stream_follow_up,
)
from app.services.session_errors import SessionVersionConflict
from app.services.session_serialization import (
    message_to_row,
    question_evaluation_record_from_row,
    question_evaluation_record_to_row,
    report_record_from_row,
    report_record_to_row,
    session_row_from_state,
    state_from_rows,
)


class PostgresInterviewSessionStore(InterviewSessionStore):
    def __init__(
        self,
        *,
        dsn: str,
        table_prefix: str = "interview",
        llm: InterviewLLM | None = None,
        knowledge_repository=None,
    ) -> None:
        super().__init__(
            llm=llm,
            knowledge_repository=knowledge_repository,
        )
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.messages_table = f"{table_prefix}_messages"
        self.reports_table = f"{table_prefix}_reports"
        self.question_evaluations_table = f"{table_prefix}_question_evaluations"
        self._ensure_schema()

    def list_runtime_tables(self) -> list[str]:
        psycopg2, _ = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
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
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT id, sequence_no, role, content, question_id
                        FROM {messages}
                        WHERE session_id = %s
                        ORDER BY sequence_no
                        """
                    ).format(messages=sql.Identifier(self.messages_table)),
                    (session_id,),
                )
                rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "sequence_no": row[1],
                "role": row[2],
                "content": row[3],
                "question_id": row[4],
            }
            for row in rows
        ]

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
        self._insert_state(state)
        return self._to_turn(state, follow_up=None)

    def get(self, session_id: str) -> InterviewState:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, plan_json, current_index, status,
                               phase, phase_status, review_status,
                               job_description, resume_text, job_tags,
                               decision_json, pending_output, skipped_question_ids,
                               started_at, finished_at, state_version,
                               checkpoint_version, last_checkpoint_at, last_command_id
                        FROM {sessions}
                        WHERE session_id = %s
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("session not found")
                session_row = self._session_row_from_db(row)
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT sequence_no, role, content, question_id
                        FROM {messages}
                        WHERE session_id = %s
                        ORDER BY sequence_no
                        """
                    ).format(messages=sql.Identifier(self.messages_table)),
                    (session_id,),
                )
                message_rows = [
                    {
                        "sequence_no": item[0],
                        "role": item[1],
                        "content": item[2],
                        "question_id": item[3],
                    }
                    for item in cursor.fetchall()
                ]
        return state_from_rows(session_row, message_rows)

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
        previous_version = state["state_version"]
        new_state = self._orchestrator.apply_command(
            state,
            {"kind": "answer", "answer": answer},
        )
        new_state = _advance_state_metadata(new_state, command_id=command_id)
        self._replace_state(new_state, expected_previous_version=previous_version)
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
        previous_version = state["state_version"]
        finished_state = self._orchestrator.apply_command(state, {"kind": "finish"})
        finished_state = _advance_state_metadata(
            finished_state,
            command_id=command_id,
        )
        self._replace_state(finished_state, expected_previous_version=previous_version)
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
        previous_version = state["state_version"]
        skipped_state = self._orchestrator.apply_command(state, {"kind": "skip"})
        skipped_state = _advance_state_metadata(
            skipped_state,
            command_id=command_id,
        )
        self._replace_state(skipped_state, expected_previous_version=previous_version)
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
            {"kind": "prepare_stream", "answer": answer},
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
        previous_version = prepared_state["state_version"]
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
        self._replace_state(finalized_state, expected_previous_version=previous_version)
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
                report=report,
                created_at=created_at,
                finished_at=report_utc_now_iso(),
            ),
        )

    def fail_report(self, session_id: str, error: str) -> None:
        state = self.get(session_id)
        existing = self.get_report_record(session_id)
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
                error=error,
                created_at=created_at,
                finished_at=report_utc_now_iso(),
            ),
        )

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT status, progress_json, report_json, error,
                               created_at, completed_at, failed_at
                        FROM {reports}
                        WHERE session_id = %s
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (session_id,),
                )
                row = cursor.fetchone()
        if row is None:
            self.get(session_id)
            return None
        return report_record_from_row(
            {
                "status": row[0],
                "progress_json": row[1],
                "report_json": row[2],
                "error": row[3],
                "created_at": self._iso_timestamp(row[4]),
                "finished_at": self._iso_timestamp(row[5] or row[6]),
            }
        )

    def list_reports(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        psycopg2, sql = self._import_psycopg2()
        where_clause = sql.SQL("")
        params: list = []
        if status is not None:
            where_clause = sql.SQL("WHERE status = %s")
            params.append(status)
        params.append(limit)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, status, progress_json, report_json, error,
                               created_at, completed_at, failed_at
                        FROM {reports}
                        {where_clause}
                        ORDER BY created_at DESC
                        LIMIT %s
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        where_clause=where_clause,
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [
            {
                "session_id": row[0],
                "record": report_record_from_row(
                    {
                        "status": row[1],
                        "progress_json": row[2],
                        "report_json": row[3],
                        "error": row[4],
                        "created_at": self._iso_timestamp(row[5]),
                        "finished_at": self._iso_timestamp(row[6] or row[7]),
                    }
                ),
            }
            for row in rows
        ]

    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                for record in records:
                    self._upsert_question_evaluation_row(cursor, sql, record)

    def upsert_question_evaluation(
        self,
        session_id: str,
        record: QuestionEvaluationRecord,
    ) -> None:
        self.get(session_id)
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                self._upsert_question_evaluation_row(cursor, sql, record)

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        self.get(session_id)
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, question_id, answer_state, status,
                               feedback_json, error, created_at
                        FROM {question_evaluations}
                        WHERE session_id = %s
                        ORDER BY question_id
                        """
                    ).format(
                        question_evaluations=sql.Identifier(
                            self.question_evaluations_table
                        )
                    ),
                    (session_id,),
                )
                rows = cursor.fetchall()
        return [
            question_evaluation_record_from_row(
                {
                    "session_id": row[0],
                    "question_id": row[1],
                    "answer_state": row[2],
                    "status": row[3],
                    "feedback_json": row[4],
                    "error": row[5],
                    "created_at": self._iso_timestamp(row[6]),
                }
            )
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {sessions} (
                            session_id TEXT PRIMARY KEY,
                            plan_json JSONB NOT NULL,
                            current_index INTEGER NOT NULL DEFAULT 0,
                            status TEXT NOT NULL CHECK (status IN ('active', 'finished')),
                            job_description TEXT NOT NULL,
                            resume_text TEXT NOT NULL,
                            job_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                            decision_json JSONB,
                            pending_output TEXT,
                            skipped_question_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            finished_at TIMESTAMPTZ
                        )
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS skipped_question_ids JSONB NOT NULL DEFAULT '[]'::jsonb"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'interview'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS phase_status TEXT NOT NULL DEFAULT 'active'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'idle'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 1"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS checkpoint_version INTEGER NOT NULL DEFAULT 1"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS last_checkpoint_at TIMESTAMPTZ"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS last_command_id TEXT"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {messages} (
                            id BIGSERIAL PRIMARY KEY,
                            session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            sequence_no INTEGER NOT NULL,
                            role TEXT NOT NULL CHECK (role IN ('interviewer', 'candidate')),
                            content TEXT NOT NULL,
                            question_id TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (session_id, sequence_no)
                        )
                        """
                    ).format(
                        messages=sql.Identifier(self.messages_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {index_name}
                        ON {messages} (session_id, sequence_no)
                        """
                    ).format(
                        index_name=sql.Identifier(f"{self.messages_table}_session_idx"),
                        messages=sql.Identifier(self.messages_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {reports} (
                            session_id TEXT PRIMARY KEY REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
                            progress_json JSONB,
                            report_json JSONB,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ,
                            failed_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {question_evaluations} (
                            session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            question_id TEXT NOT NULL,
                            answer_state TEXT NOT NULL CHECK (answer_state IN ('answered', 'skipped', 'unanswered')),
                            status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
                            feedback_json JSONB,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (session_id, question_id)
                        )
                        """
                    ).format(
                        question_evaluations=sql.Identifier(
                            self.question_evaluations_table
                        ),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )

    def _insert_state(self, state: InterviewState) -> None:
        psycopg2, sql = self._import_psycopg2()
        session_row = session_row_from_state(state)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {sessions} (
                            session_id, plan_json, current_index, status,
                            phase, phase_status, review_status,
                            job_description, resume_text, job_tags,
                            decision_json, pending_output, skipped_question_ids,
                            started_at, finished_at, state_version,
                            checkpoint_version, last_checkpoint_at, last_command_id
                        )
                        VALUES (
                            %s, %s::jsonb, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s::jsonb,
                            %s::jsonb, %s, %s::jsonb,
                            %s, %s, %s,
                            %s, %s, %s
                        )
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (
                        session_row["session_id"],
                        json.dumps(session_row["plan_json"], ensure_ascii=False),
                        session_row["current_index"],
                        session_row["status"],
                        session_row["phase"],
                        session_row["phase_status"],
                        session_row["review_status"],
                        session_row["job_description"],
                        session_row["resume_text"],
                        json.dumps(session_row["job_tags"], ensure_ascii=False),
                        json.dumps(session_row["decision_json"], ensure_ascii=False)
                        if session_row["decision_json"] is not None
                        else None,
                        session_row["pending_output"],
                        json.dumps(
                            session_row["skipped_question_ids"],
                            ensure_ascii=False,
                        ),
                        session_row["started_at"],
                        session_row["finished_at"],
                        session_row["state_version"],
                        session_row["checkpoint_version"],
                        session_row["last_checkpoint_at"],
                        session_row["last_command_id"],
                    ),
                )
                for index, message in enumerate(state["messages"], start=1):
                    message_row = message_to_row(state["session_id"], index, message)
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {messages} (
                                session_id, sequence_no, role, content, question_id
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """
                        ).format(messages=sql.Identifier(self.messages_table)),
                        (
                            message_row["session_id"],
                            message_row["sequence_no"],
                            message_row["role"],
                            message_row["content"],
                            message_row["question_id"],
                        ),
                    )

    def _replace_state(
        self,
        state: InterviewState,
        *,
        expected_previous_version: int | None = None,
    ) -> None:
        psycopg2, sql = self._import_psycopg2()
        session_row = session_row_from_state(state)
        where_clause = sql.SQL("WHERE session_id = %s")
        update_params_suffix = [session_row["session_id"]]
        if expected_previous_version is not None:
            where_clause = sql.SQL("WHERE session_id = %s AND state_version = %s")
            update_params_suffix.append(expected_previous_version)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT sequence_no, role, content, question_id
                        FROM {messages}
                        WHERE session_id = %s
                        ORDER BY sequence_no
                        """
                    ).format(messages=sql.Identifier(self.messages_table)),
                    (state["session_id"],),
                )
                existing_messages = [
                    {
                        "sequence_no": row[0],
                        "role": row[1],
                        "content": row[2],
                        "question_id": row[3],
                    }
                    for row in cursor.fetchall()
                ]

                new_message_rows = [
                    message_to_row(state["session_id"], index, message)
                    for index, message in enumerate(state["messages"], start=1)
                ]

                common_prefix = 0
                for existing, new_row in zip(existing_messages, new_message_rows):
                    if (
                        existing["sequence_no"] == new_row["sequence_no"]
                        and existing["role"] == new_row["role"]
                        and existing["content"] == new_row["content"]
                        and existing["question_id"] == new_row["question_id"]
                    ):
                        common_prefix += 1
                        continue
                    break

                if common_prefix < len(existing_messages):
                    cursor.execute(
                        sql.SQL(
                            "DELETE FROM {messages} WHERE session_id = %s AND sequence_no > %s"
                        ).format(messages=sql.Identifier(self.messages_table)),
                        (state["session_id"], common_prefix),
                    )

                for message_row in new_message_rows[common_prefix:]:
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {messages} (
                                session_id, sequence_no, role, content, question_id
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """
                        ).format(messages=sql.Identifier(self.messages_table)),
                        (
                            message_row["session_id"],
                            message_row["sequence_no"],
                            message_row["role"],
                            message_row["content"],
                            message_row["question_id"],
                        ),
                    )
                update_params = [
                    json.dumps(session_row["plan_json"], ensure_ascii=False),
                    session_row["current_index"],
                    session_row["status"],
                    session_row["phase"],
                    session_row["phase_status"],
                    session_row["review_status"],
                    session_row["job_description"],
                    session_row["resume_text"],
                    json.dumps(session_row["job_tags"], ensure_ascii=False),
                    json.dumps(session_row["decision_json"], ensure_ascii=False)
                    if session_row["decision_json"] is not None
                    else None,
                    session_row["pending_output"],
                    json.dumps(
                        session_row["skipped_question_ids"],
                        ensure_ascii=False,
                    ),
                    session_row["started_at"],
                    session_row["state_version"],
                    session_row["checkpoint_version"],
                    session_row["last_checkpoint_at"],
                    session_row["last_command_id"],
                    session_row["status"],
                    session_row["finished_at"],
                    *update_params_suffix,
                ]
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {sessions}
                        SET plan_json = %s::jsonb,
                            current_index = %s,
                            status = %s,
                            phase = %s,
                            phase_status = %s,
                            review_status = %s,
                            job_description = %s,
                            resume_text = %s,
                            job_tags = %s::jsonb,
                            decision_json = %s::jsonb,
                            pending_output = %s,
                            skipped_question_ids = %s::jsonb,
                            started_at = %s,
                            state_version = %s,
                            checkpoint_version = %s,
                            last_checkpoint_at = %s,
                            last_command_id = %s,
                            updated_at = NOW(),
                            finished_at = CASE
                                WHEN %s = 'finished' THEN COALESCE(finished_at, %s)
                                ELSE finished_at
                            END
                        {where_clause}
                        """
                    ).format(
                        sessions=sql.Identifier(self.sessions_table),
                        where_clause=where_clause,
                    ),
                    tuple(update_params),
                )
                if expected_previous_version is not None and cursor.rowcount == 0:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT state_version
                            FROM {sessions}
                            WHERE session_id = %s
                            """
                        ).format(sessions=sql.Identifier(self.sessions_table)),
                        (session_row["session_id"],),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise ValueError("session not found")
                    raise SessionVersionConflict(
                        expected_version=expected_previous_version,
                        actual_version=row[0],
                    )

    def _upsert_report_record(self, session_id: str, record: ReportRecord) -> None:
        psycopg2, sql = self._import_psycopg2()
        row = report_record_to_row(record)
        completed_finished_at = (
            row["finished_at"] if row["status"] == "completed" else None
        )
        failed_finished_at = row["finished_at"] if row["status"] == "failed" else None
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {reports} (
                            session_id, status, progress_json, report_json, error,
                            created_at, completed_at, failed_at
                        )
                        VALUES (
                            %s, %s, %s::jsonb, %s::jsonb, %s,
                            %s, %s, %s
                        )
                        ON CONFLICT (session_id) DO UPDATE
                        SET status = EXCLUDED.status,
                            progress_json = EXCLUDED.progress_json,
                            report_json = EXCLUDED.report_json,
                            error = EXCLUDED.error,
                            updated_at = NOW(),
                            completed_at = CASE
                                WHEN EXCLUDED.status = 'completed' THEN EXCLUDED.completed_at
                                ELSE {reports}.completed_at
                            END,
                            failed_at = CASE
                                WHEN EXCLUDED.status = 'failed' THEN EXCLUDED.failed_at
                                ELSE {reports}.failed_at
                            END
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (
                        session_id,
                        row["status"],
                        json.dumps(row["progress_json"], ensure_ascii=False)
                        if row["progress_json"] is not None
                        else None,
                        json.dumps(row["report_json"], ensure_ascii=False)
                        if row["report_json"] is not None
                        else None,
                        row["error"],
                        row["created_at"],
                        completed_finished_at,
                        failed_finished_at,
                    ),
                )

    def _upsert_question_evaluation_row(
        self,
        cursor,
        sql,
        record: QuestionEvaluationRecord,
    ) -> None:
        row = question_evaluation_record_to_row(record)
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {question_evaluations} (
                    session_id, question_id, answer_state, status,
                    feedback_json, error, created_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (session_id, question_id) DO UPDATE
                SET status = EXCLUDED.status,
                    answer_state = EXCLUDED.answer_state,
                    feedback_json = EXCLUDED.feedback_json,
                    error = EXCLUDED.error,
                    updated_at = NOW()
                """
            ).format(
                question_evaluations=sql.Identifier(
                    self.question_evaluations_table
                )
            ),
            (
                row["session_id"],
                row["question_id"],
                row["answer_state"],
                row["status"],
                json.dumps(row["feedback_json"], ensure_ascii=False)
                if row["feedback_json"] is not None
                else None,
                row["error"],
                row["created_at"],
            ),
        )

    @staticmethod
    def _session_row_from_db(row) -> dict:
        return {
            "session_id": row[0],
            "plan_json": row[1],
            "current_index": row[2],
            "status": row[3],
            "phase": row[4],
            "phase_status": row[5],
            "review_status": row[6],
            "job_description": row[7],
            "resume_text": row[8],
            "job_tags": row[9],
            "decision_json": row[10],
            "pending_output": row[11],
            "skipped_question_ids": row[12],
            "started_at": PostgresInterviewSessionStore._iso_timestamp(row[13]) or "",
            "finished_at": PostgresInterviewSessionStore._iso_timestamp(row[14]),
            "state_version": row[15],
            "checkpoint_version": row[16],
            "last_checkpoint_at": PostgresInterviewSessionStore._iso_timestamp(row[17]),
            "last_command_id": row[18],
        }

    @staticmethod
    def _iso_timestamp(value) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
