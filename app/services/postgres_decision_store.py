from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from app.services.decision_store import (
    DecisionAttempt,
    DecisionContract,
    DecisionNotFound,
    DecisionRecord,
    DecisionStoreConflict,
    _decision_sha256,
)
from app.services.postgres_connections import ConnectionProvider, DirectPsycopg2ConnectionProvider
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations


class PostgresDecisionStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        max_attempts: int = 2,
        lease_seconds: int = 60,
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            owned = True
        else:
            owned = False
        self._provider = connection_provider
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.decisions_table = f"{table_prefix}_followup_decisions"
        self.attempts_table = f"{table_prefix}_decision_attempts"
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.schema_mode = resolve_schema_mode(schema_mode, provider_is_owned=owned)
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._provider, (self.decisions_table, self.attempts_table))

    def prepare(self, *, session_id: str, source_command_id: str, input_sha256: str) -> DecisionRecord:
        _, sql = self._import_psycopg2()
        decision_id = str(uuid4())
        attempt_id = str(uuid4())
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT decision_id,input_sha256 FROM {decisions} "
                        "WHERE session_id=%s AND source_command_id=%s FOR UPDATE"
                    ).format(decisions=sql.Identifier(self.decisions_table)),
                    (session_id, source_command_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if existing[1] != input_sha256:
                        raise DecisionStoreConflict("source command input conflicts")
                    return self._get_decision(cursor, str(existing[0]))
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {decisions}(decision_id,session_id,source_command_id,input_sha256,max_attempts,status) "
                        "VALUES(%s::uuid,%s,%s,%s,%s,'pending')"
                    ).format(decisions=sql.Identifier(self.decisions_table)),
                    (decision_id, session_id, source_command_id, input_sha256, self.max_attempts),
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {attempts}(attempt_id,decision_id,attempt_number,status,fencing_version) "
                        "VALUES(%s::uuid,%s::uuid,1,'pending',0)"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (attempt_id, decision_id),
                )
                return self._get_decision(cursor, decision_id)

    def get(self, decision_id: str) -> DecisionRecord:
        _, sql = self._import_psycopg2()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                return self._get_decision(cursor, decision_id)

    def list_attempts(self, decision_id: str) -> list[DecisionAttempt]:
        _, sql = self._import_psycopg2()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT attempt_id,decision_id,attempt_number,status,lease_owner,lease_token,"
                        "lease_expires_at,fencing_version,error_code,output_sha256,created_at,updated_at "
                        "FROM {attempts} WHERE decision_id=%s::uuid ORDER BY attempt_number"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (decision_id,),
                )
                rows = cursor.fetchall()
                if not rows:
                    raise DecisionNotFound("decision not found")
                return [self._attempt_from_row(row) for row in rows]

    def claim(self, decision_id: str, *, worker_id: str) -> DecisionAttempt:
        _, sql = self._import_psycopg2()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                record = self._get_decision(cursor, decision_id, lock=True)
                if record.status == "completed":
                    raise DecisionStoreConflict("completed decision cannot be claimed")
                cursor.execute(
                    sql.SQL(
                        "SELECT attempt_id,decision_id,attempt_number,status,lease_owner,lease_token,"
                        "lease_expires_at,fencing_version,error_code,output_sha256,created_at,updated_at "
                        "FROM {attempts} WHERE decision_id=%s::uuid ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (decision_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise DecisionNotFound("decision attempt not found")
                attempt = self._attempt_from_row(row)
                if attempt.status == "running" and attempt.lease_expires_at and attempt.lease_expires_at > datetime.now(timezone.utc):
                    raise DecisionStoreConflict("decision attempt is leased")
                if attempt.status in {"completed", "abandoned"} or attempt.attempt_number > record.max_attempts:
                    raise DecisionStoreConflict("decision attempt is not claimable")
                token = str(uuid4())
                cursor.execute(
                    sql.SQL(
                        "UPDATE {attempts} SET status='running',lease_owner=%s,lease_token=%s::uuid,"
                        "lease_expires_at=NOW()+(%s*INTERVAL '1 second'),fencing_version=fencing_version+1,updated_at=NOW() "
                        "WHERE attempt_id=%s::uuid AND fencing_version=%s RETURNING attempt_id,decision_id,attempt_number,status,"
                        "lease_owner,lease_token,lease_expires_at,fencing_version,error_code,output_sha256,created_at,updated_at"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (worker_id, token, self.lease_seconds, attempt.attempt_id, attempt.fencing_version),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise DecisionStoreConflict("decision attempt fencing failed")
                return self._attempt_from_row(updated)

    def heartbeat(self, attempt_id: str, *, worker_id: str, lease_token: str) -> bool:
        _, sql = self._import_psycopg2()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {attempts} SET lease_expires_at=NOW()+(%s*INTERVAL '1 second'),updated_at=NOW() "
                        "WHERE attempt_id=%s::uuid AND status='running' AND lease_owner=%s AND lease_token=%s::uuid "
                        "AND lease_expires_at>NOW()"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (self.lease_seconds, attempt_id, worker_id, lease_token),
                )
                return cursor.rowcount == 1

    def complete(self, attempt_id: str, *, worker_id: str, lease_token: str, decision: DecisionContract) -> DecisionRecord:
        _, sql = self._import_psycopg2()
        digest = _decision_sha256(decision)
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT decision_id FROM {attempts} WHERE attempt_id=%s::uuid FOR UPDATE").format(
                        attempts=sql.Identifier(self.attempts_table)
                    ),
                    (attempt_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise DecisionNotFound("decision attempt not found")
                decision_id = str(row[0])
                record = self._get_decision(cursor, decision_id, lock=True)
                if record.status == "completed":
                    if record.decision_sha256 != digest:
                        raise DecisionStoreConflict("completed decision payload conflicts")
                    return record
                cursor.execute(
                    sql.SQL(
                        "UPDATE {attempts} SET status='completed',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,"
                        "output_sha256=%s,updated_at=NOW() WHERE attempt_id=%s::uuid AND status='running' AND lease_owner=%s "
                        "AND lease_token=%s::uuid AND lease_expires_at>NOW()"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (digest, attempt_id, worker_id, lease_token),
                )
                if cursor.rowcount != 1:
                    raise DecisionStoreConflict("decision attempt fencing failed")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {decisions} SET status='completed',final_decision_json=%s::jsonb,decision_sha256=%s,updated_at=NOW() "
                        "WHERE decision_id=%s::uuid"
                    ).format(decisions=sql.Identifier(self.decisions_table)),
                    (json.dumps(decision.model_dump(mode="json"), ensure_ascii=False), digest, decision_id),
                )
                return self._get_decision(cursor, decision_id)

    def fail(self, attempt_id: str, *, worker_id: str, lease_token: str, error_code: str) -> DecisionAttempt:
        _, sql = self._import_psycopg2()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT decision_id,attempt_number,fencing_version FROM {attempts} WHERE attempt_id=%s::uuid FOR UPDATE"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (attempt_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise DecisionNotFound("decision attempt not found")
                record = self._get_decision(cursor, str(row[0]), lock=True)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {attempts} SET status='failed',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,error_code=%s,updated_at=NOW() "
                        "WHERE attempt_id=%s::uuid AND status='running' AND lease_owner=%s AND lease_token=%s::uuid AND lease_expires_at>NOW() "
                        "RETURNING attempt_id,decision_id,attempt_number,status,lease_owner,lease_token,lease_expires_at,fencing_version,error_code,output_sha256,created_at,updated_at"
                    ).format(attempts=sql.Identifier(self.attempts_table)),
                    (error_code, attempt_id, worker_id, lease_token),
                )
                failed_row = cursor.fetchone()
                if failed_row is None:
                    raise DecisionStoreConflict("decision attempt fencing failed")
                if int(row[1]) < record.max_attempts:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {attempts}(attempt_id,decision_id,attempt_number,status,fencing_version) VALUES(%s::uuid,%s::uuid,%s,'pending',%s)"
                        ).format(attempts=sql.Identifier(self.attempts_table)),
                        (str(uuid4()), str(row[0]), int(row[1]) + 1, int(row[2])),
                    )
                else:
                    cursor.execute(
                        sql.SQL("UPDATE {decisions} SET status='failed',updated_at=NOW() WHERE decision_id=%s::uuid").format(
                            decisions=sql.Identifier(self.decisions_table)
                        ),
                        (str(row[0]),),
                    )
                return self._attempt_from_row(failed_row)

    def _get_decision(self, cursor, decision_id: str, *, lock: bool = False) -> DecisionRecord:
        _, sql = self._import_psycopg2()
        cursor.execute(
            sql.SQL(
                "SELECT decision_id,session_id,source_command_id,input_sha256,max_attempts,status,final_decision_json,decision_sha256,created_at,updated_at "
                "FROM {decisions} WHERE decision_id=%s::uuid"
                + (" FOR UPDATE" if lock else "")
            ).format(decisions=sql.Identifier(self.decisions_table)),
            (decision_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise DecisionNotFound("decision not found")
        final = DecisionContract.model_validate(row[6]) if row[6] is not None else None
        return DecisionRecord(
            decision_id=str(row[0]), session_id=row[1], source_command_id=row[2], input_sha256=row[3],
            max_attempts=int(row[4]), status=row[5], final_decision=final, decision_sha256=row[7], created_at=row[8], updated_at=row[9]
        )

    @staticmethod
    def _attempt_from_row(row) -> DecisionAttempt:
        return DecisionAttempt(
            attempt_id=str(row[0]), decision_id=str(row[1]), attempt_number=int(row[2]), status=row[3],
            lease_owner=row[4], lease_token=str(row[5]) if row[5] else None, lease_expires_at=row[6],
            fencing_version=int(row[7]), error_code=row[8], output_sha256=row[9], created_at=row[10], updated_at=row[11]
        )

    def _ensure_schema(self) -> None:
        _, sql = self._import_psycopg2()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {decisions}(decision_id UUID PRIMARY KEY,session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,"
                        "source_command_id TEXT NOT NULL,input_sha256 TEXT NOT NULL,max_attempts INTEGER NOT NULL CHECK(max_attempts>=1),status TEXT NOT NULL CHECK(status IN('pending','completed','failed')),"
                        "final_decision_json JSONB,decision_sha256 TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "UNIQUE(session_id,source_command_id))"
                    ).format(decisions=sql.Identifier(self.decisions_table), sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {decisions} ADD COLUMN IF NOT EXISTS max_attempts "
                        "INTEGER NOT NULL DEFAULT 2"
                    ).format(decisions=sql.Identifier(self.decisions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {attempts}(attempt_id UUID PRIMARY KEY,decision_id UUID NOT NULL REFERENCES {decisions}(decision_id) ON DELETE CASCADE,"
                        "attempt_number INTEGER NOT NULL CHECK(attempt_number>=1),status TEXT NOT NULL CHECK(status IN('pending','running','completed','failed','abandoned')),"
                        "lease_owner TEXT,lease_token UUID,lease_expires_at TIMESTAMPTZ,fencing_version INTEGER NOT NULL DEFAULT 0,error_code TEXT,output_sha256 TEXT,"
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(decision_id,attempt_number))"
                    ).format(attempts=sql.Identifier(self.attempts_table), decisions=sql.Identifier(self.decisions_table))
                )

    @staticmethod
    def _import_psycopg2():
        import psycopg2
        from psycopg2 import sql

        return psycopg2, sql
