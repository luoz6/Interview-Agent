from __future__ import annotations

from app.adapters.postgres.row_mappers.question_evaluation import (
    QuestionEvaluationRowMapper,
)
from app.adapters.postgres.row_mappers.report import ReportRowMapper
from app.adapters.postgres.row_mappers.session import (
    MessageRowMapper,
    SessionRowMapper,
)
from app.adapters.postgres.session_repository_support import postgres_sql
from app.services.postgres_connections import ConnectionProvider
from app.services.postgres_identifiers import runtime_schema_identifier


class PostgresSessionSchemaAdapter:
    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        table_prefix: str,
        sessions_table: str,
        messages_table: str,
        reports_table: str,
        question_evaluations_table: str,
    ) -> None:
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.sessions_table = sessions_table
        self.messages_table = messages_table
        self.reports_table = reports_table
        self.question_evaluations_table = question_evaluations_table

    def ensure_schema(self) -> None:
        sql = postgres_sql()
        with self._connection_provider.connection() as connection:
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
                            finished_at TIMESTAMPTZ,
                            plan_binding_json JSONB,
                            row_schema_version TEXT NOT NULL DEFAULT 'session-row-v1'
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
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS plan_binding_json JSONB"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                _ensure_row_schema_version(
                    cursor,
                    sql,
                    table_name=self.sessions_table,
                    version=SessionRowMapper.CURRENT_VERSION,
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
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS workflow_engine TEXT NOT NULL DEFAULT 'legacy' CHECK (workflow_engine IN ('legacy', 'langgraph-v1', 'langgraph-v2'))"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS graph_schema_version TEXT"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS memory_policy_version TEXT"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {sessions} SET memory_policy_version = CASE "
                        "WHEN workflow_engine = 'langgraph-v2' "
                        "THEN 'question-conversation-v1' "
                        "ELSE 'deterministic-v1' END "
                        "WHERE memory_policy_version IS NULL"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ALTER COLUMN memory_policy_version SET NOT NULL"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ALTER COLUMN memory_policy_version SET DEFAULT 'deterministic-v1'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS projection_sha256 TEXT"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS "
                        "deletion_status TEXT NOT NULL DEFAULT 'active' "
                        "CHECK (deletion_status IN ('active','deleting'))"
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
                            row_schema_version TEXT NOT NULL DEFAULT 'message-row-v1',
                            UNIQUE (session_id, sequence_no)
                        )
                        """
                    ).format(
                        messages=sql.Identifier(self.messages_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                _ensure_row_schema_version(
                    cursor,
                    sql,
                    table_name=self.messages_table,
                    version=MessageRowMapper.CURRENT_VERSION,
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {index_name}
                        ON {messages} (session_id, sequence_no)
                        """
                    ).format(
                        index_name=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix, "messages_session_idx"
                            )
                        ),
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
                            failed_at TIMESTAMPTZ,
                            row_schema_version TEXT NOT NULL DEFAULT 'report-row-v1'
                        )
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                _ensure_row_schema_version(
                    cursor,
                    sql,
                    table_name=self.reports_table,
                    version=ReportRowMapper.CURRENT_VERSION,
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
                            review_input_sha256 TEXT,
                            question_input_sha256 TEXT,
                            review_engine TEXT,
                            review_graph_schema_version TEXT,
                            output_sha256 TEXT,
                            completed_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            row_schema_version TEXT NOT NULL DEFAULT 'question-evaluation-row-v1',
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
                for column_name, column_type in (
                    ("review_input_sha256", "TEXT"),
                    ("question_input_sha256", "TEXT"),
                    ("review_engine", "TEXT"),
                    ("review_graph_schema_version", "TEXT"),
                    ("output_sha256", "TEXT"),
                    ("completed_at", "TIMESTAMPTZ"),
                ):
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {type}"
                        ).format(
                            table=sql.Identifier(self.question_evaluations_table),
                            column=sql.Identifier(column_name),
                            type=sql.SQL(column_type),
                        )
                    )
                _ensure_row_schema_version(
                    cursor,
                    sql,
                    table_name=self.question_evaluations_table,
                    version=QuestionEvaluationRowMapper.CURRENT_VERSION,
                )


def _ensure_row_schema_version(
    cursor,
    sql,
    *,
    table_name: str,
    version: str,
) -> None:
    cursor.execute(
        sql.SQL(
            "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS row_schema_version TEXT"
        ).format(table=sql.Identifier(table_name))
    )
    cursor.execute(
        sql.SQL(
            "UPDATE {table} SET row_schema_version=%s "
            "WHERE row_schema_version IS NULL"
        ).format(table=sql.Identifier(table_name)),
        (version,),
    )
    cursor.execute(
        sql.SQL(
            "ALTER TABLE {table} ALTER COLUMN row_schema_version SET NOT NULL"
        ).format(table=sql.Identifier(table_name))
    )
    cursor.execute(
        sql.SQL(
            "ALTER TABLE {table} ALTER COLUMN row_schema_version SET DEFAULT %s"
        ).format(table=sql.Identifier(table_name)),
        (version,),
    )


class PostgresRuntimeControlSchemaAdapter:
    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        table_prefix: str,
        sessions_table: str,
        outbox_table: str,
        receipts_table: str,
        agent_runs_table: str,
    ) -> None:
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.sessions_table = sessions_table
        self.outbox_table = outbox_table
        self.receipts_table = receipts_table
        self.agent_runs_table = agent_runs_table

    def ensure_schema(self) -> None:
        sql = postgres_sql()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {outbox} (
                            event_id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL
                                REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            correlation_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            schema_version TEXT NOT NULL,
                            payload_json JSONB NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'running', 'retrying',
                                    'published', 'dead_letter'
                                )),
                            attempt_count INTEGER NOT NULL DEFAULT 0
                                CHECK (attempt_count >= 0),
                            max_attempts INTEGER NOT NULL DEFAULT 5
                                CHECK (max_attempts > 0),
                            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            lease_owner TEXT,
                            lease_expires_at TIMESTAMPTZ,
                            last_error_code TEXT,
                            replay_count INTEGER NOT NULL DEFAULT 0
                                CHECK (replay_count >= 0),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            published_at TIMESTAMPTZ,
                            dead_lettered_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        outbox=sql.Identifier(self.outbox_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {receipts} (
                            event_id TEXT NOT NULL
                                REFERENCES {outbox}(event_id)
                                ON DELETE CASCADE,
                            consumer_name TEXT NOT NULL,
                            session_id TEXT NOT NULL
                                REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            correlation_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            schema_version TEXT NOT NULL,
                            status TEXT NOT NULL
                                CHECK (status IN (
                                    'running', 'retrying',
                                    'completed', 'dead_letter'
                                )),
                            attempt_count INTEGER NOT NULL DEFAULT 0
                                CHECK (attempt_count >= 0),
                            max_attempts INTEGER NOT NULL DEFAULT 5
                                CHECK (max_attempts > 0),
                            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            lease_owner TEXT,
                            lease_expires_at TIMESTAMPTZ,
                            last_error_code TEXT,
                            replay_count INTEGER NOT NULL DEFAULT 0
                                CHECK (replay_count >= 0),
                            started_at TIMESTAMPTZ,
                            completed_at TIMESTAMPTZ,
                            dead_lettered_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (event_id, consumer_name)
                        )
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table),
                        outbox=sql.Identifier(self.outbox_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {agent_runs} (
                            run_id TEXT PRIMARY KEY,
                            schema_version TEXT NOT NULL,
                            correlation_id TEXT NOT NULL,
                            causation_id TEXT,
                            parent_run_id TEXT,
                            agent TEXT NOT NULL,
                            operation TEXT NOT NULL,
                            phase TEXT NOT NULL,
                            session_id TEXT
                                REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            question_id TEXT,
                            state_version INTEGER,
                            command_id TEXT,
                            evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            attempt_number INTEGER NOT NULL DEFAULT 1
                                CHECK (attempt_number > 0),
                            status TEXT NOT NULL
                                CHECK (status IN (
                                    'completed', 'degraded',
                                    'failed', 'cancelled'
                                )),
                            started_at TIMESTAMPTZ NOT NULL,
                            finished_at TIMESTAMPTZ NOT NULL,
                            latency_ms DOUBLE PRECISION NOT NULL
                                CHECK (latency_ms >= 0),
                            fallback_reason TEXT,
                            error_code TEXT,
                            output_type TEXT,
                            safe_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                        )
                        """
                    ).format(
                        agent_runs=sql.Identifier(self.agent_runs_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {agent_runs} ADD COLUMN IF NOT EXISTS parent_run_id TEXT"
                    ).format(
                        agent_runs=sql.Identifier(self.agent_runs_table)
                    )
                )
                self._ensure_indexes(cursor, sql)

    def _ensure_indexes(self, cursor, sql) -> None:
        indexes = [
            (
                runtime_schema_identifier(
                    self.table_prefix, "runtime_outbox_status_available_idx"
                ),
                self.outbox_table,
                "status, available_at",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix, "runtime_outbox_session_idx"
                ),
                self.outbox_table,
                "session_id",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix, "runtime_outbox_correlation_idx"
                ),
                self.outbox_table,
                "correlation_id",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix,
                    "runtime_event_receipts_status_available_idx",
                ),
                self.receipts_table,
                "status, available_at",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix, "runtime_event_receipts_session_idx"
                ),
                self.receipts_table,
                "session_id",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix, "agent_runs_session_started_idx"
                ),
                self.agent_runs_table,
                "session_id, started_at",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix, "agent_runs_correlation_started_idx"
                ),
                self.agent_runs_table,
                "correlation_id, started_at",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix, "agent_runs_agent_status_started_idx"
                ),
                self.agent_runs_table,
                "agent, status, started_at",
            ),
            (
                runtime_schema_identifier(
                    self.table_prefix,
                    "agent_runs_agent_operation_started_idx",
                ),
                self.agent_runs_table,
                "agent, operation, started_at",
            ),
        ]
        for index_name, table_name, columns in indexes:
            cursor.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index} "
                    "ON {table} (" + columns + ")"
                ).format(
                    index=sql.Identifier(index_name),
                    table=sql.Identifier(table_name),
                )
            )
        cursor.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {index} "
                "ON {outbox} (lease_expires_at) "
                "WHERE status = 'running'"
            ).format(
                index=sql.Identifier(
                    runtime_schema_identifier(
                        self.table_prefix, "runtime_outbox_running_lease_idx"
                    )
                ),
                outbox=sql.Identifier(self.outbox_table),
            )
        )


__all__ = [
    "PostgresRuntimeControlSchemaAdapter",
    "PostgresSessionSchemaAdapter",
]
