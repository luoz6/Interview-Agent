from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.services.in_memory_draft_store import _validate_text
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import runtime_schema_identifier, validate_postgres_identifier
from app.services.postgres_schema import resolve_schema_mode, validate_relations


class PostgresDraftStore:
    durability = "postgres"

    def __init__(
        self,
        dsn: str | None = None,
        *,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
        ttl: timedelta = timedelta(days=7),
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("draft ttl must be positive")
        owned = connection_provider is None
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
        validate_postgres_identifier(table_prefix)
        self._provider = connection_provider
        self._table = f"{table_prefix}_interview_drafts"
        self._plans_table = f"{table_prefix}_prep_plans"
        self._ttl_seconds = int(ttl.total_seconds())
        mode = resolve_schema_mode(schema_mode, provider_is_owned=owned)
        if mode == "migrate":
            self.ensure_schema()
        else:
            validate_relations(self._provider, (self._table,))

    def ensure_schema(self) -> None:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            draft_id TEXT PRIMARY KEY,
                            job_description TEXT NOT NULL,
                            resume_text TEXT NOT NULL,
                            source_sha256 TEXT NOT NULL,
                            durability TEXT NOT NULL CHECK (durability = 'postgres'),
                            job_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                            title TEXT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL,
                            deleted_at TIMESTAMPTZ NULL,
                            CHECK (expires_at > created_at)
                        )
                        """
                    ).format(table=sql.Identifier(self._table))
                )
                cursor.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {table} (expires_at)").format(
                        index=sql.Identifier(runtime_schema_identifier(self._table.removesuffix("_interview_drafts"), "interview_drafts_expires_idx")),
                        table=sql.Identifier(self._table),
                    )
                )
            if not hasattr(self._provider, "connection_object"):
                connection.commit()

    def save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        import json
        from hashlib import sha256
        from psycopg2 import sql

        _validate_text(job_description, resume_text)
        resolved_id = draft_id or f"draft_{uuid4()}"
        source_sha256 = sha256(
            json.dumps(
                {"job_description": job_description, "resume_text": resume_text},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            draft_id, job_description, resume_text, source_sha256,
                            durability, job_tags, title,
                            created_at, updated_at, expires_at
                        ) VALUES (
                            %s, %s, %s, %s, 'postgres', %s::jsonb, %s,
                            NOW(), NOW(), NOW() + (%s * INTERVAL '1 second')
                        )
                        ON CONFLICT (draft_id) DO UPDATE SET
                            job_description = EXCLUDED.job_description,
                            resume_text = EXCLUDED.resume_text,
                            source_sha256 = EXCLUDED.source_sha256,
                            job_tags = EXCLUDED.job_tags,
                            title = EXCLUDED.title,
                            deleted_at = NULL,
                            updated_at = NOW()
                        RETURNING draft_id, job_description, resume_text, job_tags,
                                  title, created_at, updated_at, expires_at
                        """
                    ).format(table=sql.Identifier(self._table)),
                    (
                        resolved_id,
                        job_description,
                        resume_text,
                        source_sha256,
                        json.dumps(list(job_tags or [])),
                        title,
                        self._ttl_seconds,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        return self._row_payload(row)

    def get(self, draft_id: str) -> dict[str, Any]:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT draft_id, job_description, resume_text, job_tags, "
                        "title, created_at, updated_at, expires_at FROM {table} "
                        "WHERE draft_id = %s AND expires_at > NOW() AND deleted_at IS NULL"
                    ).format(table=sql.Identifier(self._table)),
                    (draft_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise ValueError("draft not found")
        return self._row_payload(row)

    def delete(self, draft_id: str) -> bool:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET deleted_at=NOW(), updated_at=NOW() "
                        "WHERE draft_id = %s AND deleted_at IS NULL"
                    ).format(
                        table=sql.Identifier(self._table)
                    ),
                    (draft_id,),
                )
                deleted = cursor.rowcount > 0
                if deleted:
                    cursor.execute("SELECT to_regclass(%s)", (self._plans_table,))
                    if cursor.fetchone()[0] is not None:
                        cursor.execute(
                            sql.SQL(
                                "DELETE FROM {plans} WHERE source_draft_id=%s "
                                "AND state <> 'consumed'"
                            ).format(plans=sql.Identifier(self._plans_table)),
                            (draft_id,),
                        )
            connection.commit()
        return deleted

    def _row_payload(self, row) -> dict[str, Any]:
        if row is None:
            raise RuntimeError("draft write did not return a row")
        return {
            "draft_id": row[0],
            "job_description": row[1],
            "resume_text": row[2],
            "job_tags": list(row[3] or []),
            "title": row[4],
            "created_at": row[5].isoformat(),
            "updated_at": row[6].isoformat(),
            "expires_at": row[7].isoformat(),
            "durability": self.durability,
        }
