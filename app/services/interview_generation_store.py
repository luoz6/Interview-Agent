from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Callable


class GenerationAlreadyCompleted(RuntimeError):
    pass


class GenerationLeaseConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class InterviewGeneration:
    generation_id: str
    session_id: str
    source_command_id: str
    question_id: str
    status: str
    active_attempt: int
    final_text: str | None


@dataclass(frozen=True)
class GenerationAttempt:
    generation_id: str
    attempt_number: int
    status: str
    lease_owner: str | None


@dataclass(frozen=True)
class GenerationEvent:
    generation_id: str
    attempt_number: int
    sequence: int
    event_type: str
    delta: str


class PostgresInterviewGenerationStore:
    def __init__(self, *, dsn: str, table_prefix: str = "interview") -> None:
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.generations_table = f"{table_prefix}_generations"
        self.attempts_table = f"{table_prefix}_generation_attempts"
        self.chunks_table = f"{table_prefix}_generation_chunks"
        self._ensure_schema()

    def prepare_generation(
        self,
        *,
        session_id: str,
        source_command_id: str,
        question_id: str,
    ) -> InterviewGeneration:
        generation_id = "generation-" + hashlib.sha256(
            f"{session_id}:{source_command_id}".encode("utf-8")
        ).hexdigest()[:32]
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {generations} (
                            generation_id, session_id, source_command_id,
                            question_id, status, active_attempt
                        )
                        VALUES (%s, %s, %s, %s, 'pending', 1)
                        ON CONFLICT (session_id, source_command_id) DO NOTHING
                        """
                    ),
                    (generation_id, session_id, source_command_id, question_id),
                )
                cursor.execute(
                    self._sql(
                        """
                        SELECT generation_id, session_id, source_command_id,
                               question_id, status, active_attempt, final_text
                        FROM {generations}
                        WHERE session_id = %s AND source_command_id = %s
                        """
                    ),
                    (session_id, source_command_id),
                )
                row = cursor.fetchone()
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {attempts} (
                            generation_id, attempt_number, status
                        )
                        VALUES (%s, 1, 'pending')
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    (row[0],),
                )
        return InterviewGeneration(*row)

    def start_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        *,
        worker_id: str = "worker",
        lease_seconds: int = 60,
    ) -> GenerationAttempt:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT status, active_attempt FROM {generations}
                        WHERE generation_id = %s FOR UPDATE
                        """
                    ),
                    (generation_id,),
                )
                generation = cursor.fetchone()
                if generation is None:
                    raise ValueError("generation not found")
                if generation[0] == "completed":
                    raise GenerationAlreadyCompleted(generation_id)
                if attempt_number < generation[1] or attempt_number > 3:
                    raise GenerationLeaseConflict(generation_id)
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {attempts} (
                            generation_id, attempt_number, status,
                            lease_owner, lease_expires_at, started_at
                        )
                        VALUES (
                            %s, %s, 'running', %s,
                            NOW() + (%s * INTERVAL '1 second'), NOW()
                        )
                        ON CONFLICT (generation_id, attempt_number) DO UPDATE
                        SET status = 'running', lease_owner = EXCLUDED.lease_owner,
                            lease_expires_at = EXCLUDED.lease_expires_at,
                            started_at = COALESCE({attempts}.started_at, NOW()),
                            updated_at = NOW()
                        WHERE {attempts}.status IN ('pending', 'failed', 'abandoned')
                        RETURNING generation_id, attempt_number, status, lease_owner
                        """
                    ),
                    (generation_id, attempt_number, worker_id, lease_seconds),
                )
                row = cursor.fetchone()
                if row is None:
                    raise GenerationLeaseConflict(generation_id)
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {generations}
                        SET status = 'running', active_attempt = %s, updated_at = NOW()
                        WHERE generation_id = %s
                        """
                    ),
                    (attempt_number, generation_id),
                )
                if attempt_number > 1:
                    self._insert_event(
                        cursor,
                        generation_id,
                        attempt_number,
                        0,
                        "generation_reset",
                        "",
                    )
        return GenerationAttempt(*row)

    def start_or_reclaim_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> GenerationAttempt:
        try:
            return self.start_attempt(
                generation_id,
                attempt_number,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        except GenerationLeaseConflict:
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT status, lease_owner, lease_expires_at <= NOW()
                            FROM {attempts}
                            WHERE generation_id = %s AND attempt_number = %s
                            """
                        ),
                        (generation_id, attempt_number),
                    )
                    row = cursor.fetchone()
            if row and row[0] == "running" and row[1] == worker_id:
                return GenerationAttempt(
                    generation_id, attempt_number, "running", worker_id
                )
            raise

    def append_chunk(
        self,
        generation_id: str,
        attempt_number: int,
        sequence: int,
        delta: str,
    ) -> None:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                inserted = self._insert_event(
                    cursor,
                    generation_id,
                    attempt_number,
                    sequence,
                    "chunk",
                    delta,
                )
                if not inserted:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT event_type, delta FROM {chunks}
                            WHERE generation_id = %s
                              AND attempt_number = %s AND sequence = %s
                            """
                        ),
                        (generation_id, attempt_number, sequence),
                    )
                    if cursor.fetchone() != ("chunk", delta):
                        raise ValueError("generation chunk conflict")

    def abandon_attempt(
        self, generation_id: str, attempt_number: int, error_code: str
    ) -> None:
        self._set_attempt_status(
            generation_id, attempt_number, "abandoned", error_code
        )

    def fail_attempt(
        self, generation_id: str, attempt_number: int, error_code: str
    ) -> None:
        self._set_attempt_status(
            generation_id, attempt_number, "failed", error_code
        )

    def complete_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        final_text: str,
    ) -> None:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {attempts}
                        SET status = 'completed', completed_at = NOW(),
                            lease_owner = NULL, lease_expires_at = NULL,
                            updated_at = NOW()
                        WHERE generation_id = %s AND attempt_number = %s
                          AND status = 'running'
                        """
                    ),
                    (generation_id, attempt_number),
                )
                if cursor.rowcount != 1:
                    raise GenerationLeaseConflict(generation_id)
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {generations}
                        SET status = 'completed', final_text = %s,
                            completed_at = NOW(), updated_at = NOW()
                        WHERE generation_id = %s AND active_attempt = %s
                          AND status <> 'completed'
                        """
                    ),
                    (final_text, generation_id, attempt_number),
                )
                if cursor.rowcount != 1:
                    raise GenerationAlreadyCompleted(generation_id)

    def heartbeat_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> bool:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {attempts}
                        SET lease_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            updated_at = NOW()
                        WHERE generation_id = %s AND attempt_number = %s
                          AND status = 'running' AND lease_owner = %s
                        """
                    ),
                    (
                        lease_seconds,
                        generation_id,
                        attempt_number,
                        worker_id,
                    ),
                )
                return cursor.rowcount == 1

    def list_events(self, generation_id: str) -> list[GenerationEvent]:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT generation_id, attempt_number, sequence,
                               event_type, delta
                        FROM {chunks}
                        WHERE generation_id = %s
                        ORDER BY attempt_number, sequence
                        """
                    ),
                    (generation_id,),
                )
                return [GenerationEvent(*row) for row in cursor.fetchall()]

    def get_by_source_command(
        self, session_id: str, source_command_id: str
    ) -> InterviewGeneration | None:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT generation_id, session_id, source_command_id,
                               question_id, status, active_attempt, final_text
                        FROM {generations}
                        WHERE session_id = %s AND source_command_id = %s
                        """
                    ),
                    (session_id, source_command_id),
                )
                row = cursor.fetchone()
        return InterviewGeneration(*row) if row is not None else None

    def cleanup_completed_chunks(self, *, older_than: datetime) -> int:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        DELETE FROM {chunks}
                        USING {generations}
                        WHERE {chunks}.generation_id =
                              {generations}.generation_id
                          AND {generations}.status = 'completed'
                          AND {generations}.completed_at < %s
                        """
                    ),
                    (older_than,),
                )
                return cursor.rowcount

    def delete_session_rows(self, session_id: str) -> int:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "DELETE FROM {generations} WHERE session_id = %s"
                    ),
                    (session_id,),
                )
                return cursor.rowcount

    def count_session_rows(self, session_id: str) -> int:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "SELECT COUNT(*) FROM {generations} WHERE session_id = %s"
                    ),
                    (session_id,),
                )
                return int(cursor.fetchone()[0])

    def _set_attempt_status(
        self,
        generation_id: str,
        attempt_number: int,
        status: str,
        error_code: str,
    ) -> None:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {attempts}
                        SET status = %s, last_error_code = %s,
                            lease_owner = NULL, lease_expires_at = NULL,
                            completed_at = NOW(), updated_at = NOW()
                        WHERE generation_id = %s AND attempt_number = %s
                          AND status IN ('running', 'pending')
                        """
                    ),
                    (status, error_code, generation_id, attempt_number),
                )
                if cursor.rowcount != 1:
                    raise GenerationLeaseConflict(generation_id)

    def _insert_event(
        self,
        cursor,
        generation_id: str,
        attempt_number: int,
        sequence: int,
        event_type: str,
        delta: str,
    ) -> bool:
        cursor.execute(
            self._sql(
                """
                INSERT INTO {chunks} (
                    generation_id, attempt_number, sequence, event_type, delta
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """
            ),
            (generation_id, attempt_number, sequence, event_type, delta),
        )
        return cursor.rowcount == 1

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        CREATE TABLE IF NOT EXISTS {generations} (
                            generation_id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            source_command_id TEXT NOT NULL,
                            question_id TEXT NOT NULL,
                            status TEXT NOT NULL CHECK (
                                status IN ('pending','running','completed','failed')
                            ),
                            active_attempt INTEGER NOT NULL DEFAULT 1,
                            final_text TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (session_id, source_command_id)
                        )
                        """
                    )
                )
                cursor.execute(
                    self._sql(
                        """
                        CREATE TABLE IF NOT EXISTS {attempts} (
                            generation_id TEXT NOT NULL
                                REFERENCES {generations}(generation_id)
                                ON DELETE CASCADE,
                            attempt_number INTEGER NOT NULL CHECK (
                                attempt_number BETWEEN 1 AND 3
                            ),
                            status TEXT NOT NULL CHECK (
                                status IN (
                                    'pending','running','completed',
                                    'failed','abandoned'
                                )
                            ),
                            lease_owner TEXT,
                            lease_expires_at TIMESTAMPTZ,
                            last_error_code TEXT,
                            started_at TIMESTAMPTZ,
                            completed_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (generation_id, attempt_number)
                        )
                        """
                    )
                )
                cursor.execute(
                    self._sql(
                        """
                        CREATE TABLE IF NOT EXISTS {chunks} (
                            generation_id TEXT NOT NULL,
                            attempt_number INTEGER NOT NULL,
                            sequence INTEGER NOT NULL CHECK (sequence >= 0),
                            event_type TEXT NOT NULL CHECK (
                                event_type IN ('chunk','generation_reset')
                            ),
                            delta TEXT NOT NULL DEFAULT '',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (
                                generation_id, attempt_number, sequence
                            ),
                            FOREIGN KEY (generation_id, attempt_number)
                                REFERENCES {attempts}(
                                    generation_id, attempt_number
                                ) ON DELETE CASCADE
                        )
                        """
                    )
                )
                from psycopg2 import sql

                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {index}
                        ON {generations} (session_id, source_command_id)
                        """
                    ).format(
                        index=sql.Identifier(
                            f"{self.generations_table}_session_source_idx"
                        ),
                        generations=sql.Identifier(self.generations_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {index}
                        ON {chunks} (
                            generation_id, attempt_number, sequence
                        )
                        """
                    ).format(
                        index=sql.Identifier(
                            f"{self.chunks_table}_replay_idx"
                        ),
                        chunks=sql.Identifier(self.chunks_table),
                    )
                )

    def _connection(self):
        import psycopg2

        return psycopg2.connect(self.dsn)

    def _sql(self, statement: str):
        from psycopg2 import sql

        return sql.SQL(statement).format(
            sessions=sql.Identifier(self.sessions_table),
            generations=sql.Identifier(self.generations_table),
            attempts=sql.Identifier(self.attempts_table),
            chunks=sql.Identifier(self.chunks_table),
        )


class ChunkCoalescer:
    def __init__(
        self,
        *,
        max_interval_seconds: float = 0.2,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.max_interval_seconds = max_interval_seconds
        self._clock = clock
        self._parts: list[str] = []
        self._started = clock()

    def add(self, value: str) -> str | None:
        self._parts.append(value)
        if self._clock() - self._started < self.max_interval_seconds:
            return None
        return self.flush()

    def flush(self) -> str | None:
        if not self._parts:
            return None
        value = "".join(self._parts)
        self._parts.clear()
        self._started = self._clock()
        return value
