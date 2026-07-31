from __future__ import annotations

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.session_deletion_tombstones import (
    SessionDeletionTombstone,
    build_tombstone,
    validate_tombstone_integrity,
)


class PostgresSessionDeletionTombstoneStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            provider_is_owned = True
        else:
            provider_is_owned = False
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.table = f"{table_prefix}_session_deletion_tombstones"
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (self.table, f"{table_prefix}_schema_migrations"),
            )

    def record_requested(self, job) -> SessionDeletionTombstone:
        item = build_tombstone(
            deletion_job_id=job.job_id,
            session_id=job.session_id,
            requested_at=job.created_at,
            updated_at=job.updated_at,
        )
        return self._upsert(item)

    def record_completed(self, job) -> SessionDeletionTombstone:
        from datetime import datetime, timezone

        existing = self.get_for_session(job.session_id)
        item = build_tombstone(
            deletion_job_id=job.job_id,
            session_id=job.session_id,
            requested_at=(
                existing.requested_at if existing is not None else job.created_at
            ),
            completed_at=job.completed_at or datetime.now(timezone.utc),
            replay_status="completed",
            updated_at=job.updated_at,
        )
        return self._upsert(item)

    def import_tombstone(
        self, tombstone: SessionDeletionTombstone
    ) -> SessionDeletionTombstone:
        validate_tombstone_integrity(tombstone)
        return self._upsert(tombstone)

    def mark_replayed(
        self, tombstone: SessionDeletionTombstone
    ) -> SessionDeletionTombstone:
        validate_tombstone_integrity(tombstone)
        from datetime import datetime, timezone

        current = datetime.now(timezone.utc)
        return self._upsert(
            tombstone.model_copy(
                update={
                    "replay_status": "replayed",
                    "replayed_at": current,
                    "updated_at": current,
                }
            )
        )

    def get_for_session(
        self, session_id: str
    ) -> SessionDeletionTombstone | None:
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._select_sql(sql, "WHERE session_id = %s"),
                    (session_id,),
                )
                row = cursor.fetchone()
        return None if row is None else self._from_row(row)

    def list_completed(
        self, *, limit: int = 1000
    ) -> list[SessionDeletionTombstone]:
        if limit < 1 or limit > 10_000:
            raise ValueError("tombstone limit is out of range")
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._select_sql(
                        sql,
                        "WHERE replay_status IN ('completed','replayed') "
                        "ORDER BY requested_at, deletion_job_id LIMIT %s",
                    ),
                    (limit,),
                )
                rows = cursor.fetchall()
        return [self._from_row(row) for row in rows]

    def _upsert(
        self, tombstone: SessionDeletionTombstone
    ) -> SessionDeletionTombstone:
        validate_tombstone_integrity(tombstone)
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            session_id, deletion_job_id, requested_at,
                            completed_at, policy_version, replay_status,
                            integrity_sha256, replayed_at, updated_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (session_id) DO UPDATE SET
                            deletion_job_id = EXCLUDED.deletion_job_id,
                            requested_at = EXCLUDED.requested_at,
                            completed_at = COALESCE(
                                EXCLUDED.completed_at,
                                {table}.completed_at
                            ),
                            policy_version = EXCLUDED.policy_version,
                            replay_status = EXCLUDED.replay_status,
                            integrity_sha256 = EXCLUDED.integrity_sha256,
                            replayed_at = EXCLUDED.replayed_at,
                            updated_at = EXCLUDED.updated_at
                        RETURNING session_id, deletion_job_id, requested_at,
                                  completed_at, policy_version, replay_status,
                                  integrity_sha256, replayed_at, updated_at
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        tombstone.session_id,
                        tombstone.deletion_job_id,
                        tombstone.requested_at,
                        tombstone.completed_at,
                        tombstone.policy_version,
                        tombstone.replay_status,
                        tombstone.integrity_sha256,
                        tombstone.replayed_at,
                        tombstone.updated_at,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        return self._from_row(row)

    def _ensure_schema(self) -> None:
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            session_id TEXT PRIMARY KEY,
                            deletion_job_id TEXT NOT NULL,
                            requested_at TIMESTAMPTZ NOT NULL,
                            completed_at TIMESTAMPTZ,
                            policy_version TEXT NOT NULL,
                            replay_status TEXT NOT NULL CHECK (
                                replay_status IN (
                                    'requested','completed','replayed','failed'
                                )
                            ),
                            integrity_sha256 TEXT NOT NULL CHECK (
                                integrity_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            replayed_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} "
                        "(replay_status, requested_at)"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix,
                                "deletion_tombstone_status_idx",
                            )
                        ),
                        table=sql.Identifier(self.table),
                    )
                )
            connection.commit()

    def _select_sql(self, sql, suffix):
        return sql.SQL(
            "SELECT session_id, deletion_job_id, requested_at, completed_at, "
            "policy_version, replay_status, integrity_sha256, replayed_at, "
            "updated_at FROM {table} " + suffix
        ).format(table=sql.Identifier(self.table))

    @staticmethod
    def _from_row(row) -> SessionDeletionTombstone:
        return SessionDeletionTombstone(
            session_id=row[0],
            deletion_job_id=row[1],
            requested_at=row[2],
            completed_at=row[3],
            policy_version=row[4],
            replay_status=row[5],
            integrity_sha256=row[6],
            replayed_at=row[7],
            updated_at=row[8],
        )
