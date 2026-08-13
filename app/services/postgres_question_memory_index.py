from __future__ import annotations

import json

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.question_memory_index import QuestionMemoryIndexEntry


class PostgresQuestionMemoryIndexStore:
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
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self._connection_provider = connection_provider
        self.dsn = dsn or ""
        self.table_prefix = table_prefix
        self.table = f"{table_prefix}_question_memory_refs"
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=self._provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def activate(self, entry: QuestionMemoryIndexEntry) -> QuestionMemoryIndexEntry:
        if entry.status != "active":
            raise ValueError("new question memory index entry must be active")
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT artifact_ref FROM {table} "
                        "WHERE session_id=%s AND question_id=%s "
                        "AND policy_version=%s AND status='active' FOR UPDATE"
                    ).format(table=sql.Identifier(self.table)),
                    (entry.session_id, entry.question_id, entry.policy_version),
                )
                row = cursor.fetchone()
                previous_ref = row[0] if row else None
                if previous_ref == entry.artifact_ref:
                    return entry
                if previous_ref is not None:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {table} SET status='superseded', "
                            "superseded_at=NOW() WHERE artifact_ref=%s"
                        ).format(table=sql.Identifier(self.table)),
                        (previous_ref,),
                    )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            session_id, question_id, question_id_sha256,
                            focus_sha256, focus_tags, skill_tags,
                            skill_tag_sha256, unresolved_topic_codes,
                            unresolved_topic_sha256, artifact_ref,
                            artifact_sha256, artifact_type, policy_version,
                            source_manifest_sha256, source_message_count,
                            source_max_sequence_no, taxonomy_version, status,
                            supersedes_artifact_ref, created_at,
                            resolved_target_output_tokens
                        ) VALUES (
                            %s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,
                            %s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,
                            'active',%s,%s,%s
                        )
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        entry.session_id,
                        entry.question_id,
                        entry.question_id_sha256,
                        entry.focus_sha256,
                        json.dumps(entry.focus_tags),
                        json.dumps(entry.skill_tags),
                        json.dumps(entry.skill_tag_sha256),
                        json.dumps(entry.unresolved_topic_codes),
                        json.dumps(entry.unresolved_topic_sha256),
                        entry.artifact_ref,
                        entry.artifact_sha256,
                        entry.artifact_type,
                        entry.policy_version,
                        entry.source_manifest_sha256,
                        entry.source_message_count,
                        entry.source_max_sequence_no,
                        entry.taxonomy_version,
                        previous_ref,
                        entry.created_at,
                        entry.resolved_target_output_tokens,
                    ),
                )
            connection.commit()
        return entry.model_copy(update={"supersedes_artifact_ref": previous_ref})

    def get_active(self, *, session_id, question_id, policy_version):
        return self._fetch_one(
            "session_id=%s AND question_id=%s AND policy_version=%s AND status='active'",
            (session_id, question_id, policy_version),
        )

    def list_active(self, *, session_id, policy_version, limit):
        if limit <= 0:
            raise ValueError("question memory list limit must be positive")
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"SELECT {self._columns()} FROM {{table}} "
                        "WHERE session_id=%s AND policy_version=%s "
                        "AND status='active' "
                        "ORDER BY source_max_sequence_no DESC, created_at DESC "
                        "LIMIT %s"
                    ).format(table=sql.Identifier(self.table)),
                    (session_id, policy_version, limit),
                )
                return [self._from_row(row) for row in cursor.fetchall()]

    def get_historical(self, artifact_ref):
        return self._fetch_one("artifact_ref=%s", (artifact_ref,))

    def mark_session_deleted(self, session_id):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET status='deleted', deleted_at=NOW() "
                        "WHERE session_id=%s AND status <> 'deleted'"
                    ).format(table=sql.Identifier(self.table)),
                    (session_id,),
                )
                count = cursor.rowcount
            connection.commit()
        return count

    def delete_session(self, session_id):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {table} WHERE session_id=%s").format(
                        table=sql.Identifier(self.table)
                    ),
                    (session_id,),
                )
                count = cursor.rowcount
            connection.commit()
        return count

    def _fetch_one(self, where, params):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"SELECT {self._columns()} FROM {{table}} WHERE {where}"
                    ).format(table=sql.Identifier(self.table)),
                    params,
                )
                row = cursor.fetchone()
        return self._from_row(row) if row is not None else None

    @staticmethod
    def _columns():
        return (
            "session_id,question_id,question_id_sha256,focus_sha256,"
            "focus_tags,skill_tags,skill_tag_sha256,unresolved_topic_codes,"
            "unresolved_topic_sha256,artifact_ref,artifact_sha256,artifact_type,"
            "policy_version,source_manifest_sha256,source_message_count,"
            "source_max_sequence_no,taxonomy_version,status,"
            "supersedes_artifact_ref,created_at,superseded_at,deleted_at,"
            "resolved_target_output_tokens"
        )

    @staticmethod
    def _from_row(row):
        return QuestionMemoryIndexEntry(
            session_id=row[0],
            question_id=row[1],
            question_id_sha256=row[2],
            focus_sha256=row[3],
            focus_tags=list(row[4]),
            skill_tags=list(row[5]),
            skill_tag_sha256=list(row[6]),
            unresolved_topic_codes=list(row[7]),
            unresolved_topic_sha256=list(row[8]),
            artifact_ref=row[9],
            artifact_sha256=row[10],
            artifact_type=row[11],
            policy_version=row[12],
            source_manifest_sha256=row[13],
            source_message_count=row[14],
            source_max_sequence_no=row[15],
            taxonomy_version=row[16],
            status=row[17],
            supersedes_artifact_ref=row[18],
            created_at=row[19],
            superseded_at=row[20],
            deleted_at=row[21],
            resolved_target_output_tokens=row[22],
        )

    def _ensure_schema(self):
        from psycopg2 import sql

        table = sql.Identifier(self.table)
        resolved_target_check = sql.Identifier(
            runtime_schema_identifier(
                self.table_prefix,
                "question_memory_resolved_target_check",
            )
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            session_id TEXT NOT NULL,
                            question_id TEXT NOT NULL,
                            question_id_sha256 TEXT NOT NULL CHECK (question_id_sha256 ~ '^[0-9a-f]{{64}}$'),
                            focus_sha256 TEXT NOT NULL CHECK (focus_sha256 ~ '^[0-9a-f]{{64}}$'),
                            focus_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                            skill_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                            skill_tag_sha256 JSONB NOT NULL DEFAULT '[]'::jsonb,
                            unresolved_topic_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
                            unresolved_topic_sha256 JSONB NOT NULL DEFAULT '[]'::jsonb,
                            artifact_ref TEXT PRIMARY KEY,
                            artifact_sha256 TEXT NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{{64}}$'),
                            artifact_type TEXT NOT NULL CHECK (artifact_type='question_memory'),
                            policy_version TEXT NOT NULL,
                            source_manifest_sha256 TEXT NOT NULL CHECK (source_manifest_sha256 ~ '^[0-9a-f]{{64}}$'),
                            source_message_count INTEGER NOT NULL CHECK (source_message_count > 0),
                            source_max_sequence_no INTEGER NOT NULL CHECK (source_max_sequence_no > 0),
                            taxonomy_version TEXT NOT NULL,
                            status TEXT NOT NULL CHECK (status IN ('active','superseded','deleted')),
                            supersedes_artifact_ref TEXT,
                            created_at TIMESTAMPTZ NOT NULL,
                            superseded_at TIMESTAMPTZ,
                            deleted_at TIMESTAMPTZ,
                            resolved_target_output_tokens INTEGER,
                            CONSTRAINT {resolved_target_check}
                                CHECK (resolved_target_output_tokens > 0)
                        )
                        """
                    ).format(
                        table=table,
                        resolved_target_check=resolved_target_check,
                    )
                )
                active_index = runtime_schema_identifier(
                    self.table_prefix,
                    "question_memory_active_idx",
                )
                session_index = runtime_schema_identifier(
                    self.table_prefix,
                    "question_memory_session_idx",
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} "
                        "(session_id, question_id, policy_version) "
                        "WHERE status='active'"
                    ).format(index=sql.Identifier(active_index), table=table)
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} "
                        "(session_id, policy_version, source_max_sequence_no DESC)"
                    ).format(index=sql.Identifier(session_index), table=table)
                )
            connection.commit()
