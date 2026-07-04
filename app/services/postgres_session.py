import json
from uuid import uuid4

from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.session import (
    InterviewSessionStore,
    InterviewTurn,
    PreparedInterviewTurn,
    finish_interview_state,
    skip_interview_question_state,
)
from app.services.session_serialization import (
    message_to_row,
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
    ) -> None:
        super().__init__(llm=llm)
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.messages_table = f"{table_prefix}_messages"
        self.reports_table = f"{table_prefix}_reports"
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
                    ([self.sessions_table, self.messages_table, self.reports_table],),
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
                               job_description, resume_text, job_tags,
                               decision_json, pending_output
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

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")
        state = self.get(session_id)
        new_state = self._runner.submit_answer(state, answer)
        self._replace_state(new_state)
        return self._to_turn(new_state, follow_up=self._extract_follow_up(new_state))

    def finish(self, session_id: str) -> InterviewTurn:
        state = self.get(session_id)
        finished_state = finish_interview_state(state)
        self._replace_state(finished_state)
        return self._to_turn(
            finished_state,
            follow_up=self._extract_follow_up(finished_state),
        )

    def skip(self, session_id: str) -> InterviewTurn:
        state = self.get(session_id)
        skipped_state = skip_interview_question_state(state)
        self._replace_state(skipped_state)
        return self._to_turn(
            skipped_state,
            follow_up=self._extract_follow_up(skipped_state),
        )

    def prepare_streaming_answer(self, session_id: str, answer: str) -> PreparedInterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")
        state = self.get(session_id)
        prepared_state = self._runner.prepare_answer(state, answer)
        self._replace_state(prepared_state)
        decision = prepared_state["decision"]
        should_stream = bool(decision and decision["action"] == "follow_up")
        return PreparedInterviewTurn(
            state=prepared_state,
            stream_follow_up=should_stream,
        )

    def complete_streaming_answer(
        self,
        session_id: str,
        *,
        follow_up_text: str | None = None,
    ) -> InterviewState:
        prepared_state = self.get(session_id)
        if self._already_completed_streaming_followup(prepared_state, follow_up_text):
            return prepared_state
        finalized_state = self._runner.finalize_prepared_answer(
            prepared_state,
            follow_up=follow_up_text,
        )
        self._replace_state(finalized_state)
        return finalized_state

    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if self.get_report_record(session_id) is not None:
            return False
        record = ReportRecord(
            status="processing",
            progress=ReportProgress(
                stage="retrieving",
                percent=20,
                message="Retrieving role-specific knowledge references.",
            ),
        )
        self._upsert_report_record(session_id, record)
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
            ReportRecord(status="processing", progress=progress),
        )

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        self.get(session_id)
        self._upsert_report_record(
            session_id,
            ReportRecord(status="completed", report=report),
        )

    def fail_report(self, session_id: str, error: str) -> None:
        self.get(session_id)
        self._upsert_report_record(
            session_id,
            ReportRecord(status="failed", error=error),
        )

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT status, progress_json, report_json, error
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
            }
        )

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
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            finished_at TIMESTAMPTZ
                        )
                        """
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
                            job_description, resume_text, job_tags,
                            decision_json, pending_output, finished_at
                        )
                        VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                                CASE WHEN %s = 'finished' THEN NOW() ELSE NULL END)
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (
                        session_row["session_id"],
                        json.dumps(session_row["plan_json"], ensure_ascii=False),
                        session_row["current_index"],
                        session_row["status"],
                        session_row["job_description"],
                        session_row["resume_text"],
                        json.dumps(session_row["job_tags"], ensure_ascii=False),
                        json.dumps(session_row["decision_json"], ensure_ascii=False)
                        if session_row["decision_json"] is not None
                        else None,
                        session_row["pending_output"],
                        session_row["status"],
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

    def _replace_state(self, state: InterviewState) -> None:
        psycopg2, sql = self._import_psycopg2()
        session_row = session_row_from_state(state)
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
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {sessions}
                        SET plan_json = %s::jsonb,
                            current_index = %s,
                            status = %s,
                            job_description = %s,
                            resume_text = %s,
                            job_tags = %s::jsonb,
                            decision_json = %s::jsonb,
                            pending_output = %s,
                            updated_at = NOW(),
                            finished_at = CASE
                                WHEN %s = 'finished' THEN COALESCE(finished_at, NOW())
                                ELSE finished_at
                            END
                        WHERE session_id = %s
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (
                        json.dumps(session_row["plan_json"], ensure_ascii=False),
                        session_row["current_index"],
                        session_row["status"],
                        session_row["job_description"],
                        session_row["resume_text"],
                        json.dumps(session_row["job_tags"], ensure_ascii=False),
                        json.dumps(session_row["decision_json"], ensure_ascii=False)
                        if session_row["decision_json"] is not None
                        else None,
                        session_row["pending_output"],
                        session_row["status"],
                        session_row["session_id"],
                    ),
                )

    def _upsert_report_record(self, session_id: str, record: ReportRecord) -> None:
        psycopg2, sql = self._import_psycopg2()
        row = report_record_to_row(record)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {reports} (
                            session_id, status, progress_json, report_json, error,
                            completed_at, failed_at
                        )
                        VALUES (
                            %s, %s, %s::jsonb, %s::jsonb, %s,
                            CASE WHEN %s = 'completed' THEN NOW() ELSE NULL END,
                            CASE WHEN %s = 'failed' THEN NOW() ELSE NULL END
                        )
                        ON CONFLICT (session_id) DO UPDATE
                        SET status = EXCLUDED.status,
                            progress_json = EXCLUDED.progress_json,
                            report_json = EXCLUDED.report_json,
                            error = EXCLUDED.error,
                            updated_at = NOW(),
                            completed_at = CASE
                                WHEN EXCLUDED.status = 'completed' THEN NOW()
                                ELSE {reports}.completed_at
                            END,
                            failed_at = CASE
                                WHEN EXCLUDED.status = 'failed' THEN NOW()
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
                        row["status"],
                        row["status"],
                    ),
                )

    @staticmethod
    def _session_row_from_db(row) -> dict:
        return {
            "session_id": row[0],
            "plan_json": row[1],
            "current_index": row[2],
            "status": row[3],
            "job_description": row[4],
            "resume_text": row[5],
            "job_tags": row[6],
            "decision_json": row[7],
            "pending_output": row[8],
        }

    @staticmethod
    def _extract_follow_up(state: InterviewState) -> str | None:
        decision = state["decision"]
        if decision and decision["action"] == "follow_up":
            return state["pending_output"]
        if state["status"] == "finished":
            return state["pending_output"]
        return None

    @staticmethod
    def _already_completed_streaming_followup(
        state: InterviewState,
        follow_up_text: str | None,
    ) -> bool:
        if not follow_up_text or not state["messages"]:
            return False
        last = state["messages"][-1]
        return last["role"] == "interviewer" and last["content"] == follow_up_text

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
