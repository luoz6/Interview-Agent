from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from hashlib import sha256
from typing import Any
from uuid import uuid4

from app.domain.interview.drafts import (
    DraftWriteConflict,
    plan_status,
    validate_plan_binding,
)
from app.services.in_memory_draft_store import _validate_text
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations


def ensure_postgres_draft_plan_binding_schema(
    connection,
    *,
    table_prefix: str,
) -> None:
    """Install the nullable V18 binding contract without rewriting legacy rows."""

    from psycopg2 import sql

    table = f"{table_prefix}_interview_drafts"
    revisions = f"{table_prefix}_plan_revisions"
    binding_check = runtime_schema_identifier(
        table_prefix, "interview_drafts_plan_binding_check"
    )
    version_check = runtime_schema_identifier(
        table_prefix, "interview_drafts_version_check"
    )
    revision_fk = runtime_schema_identifier(
        table_prefix, "interview_drafts_plan_revision_fk"
    )
    revision_index = runtime_schema_identifier(
        table_prefix, "interview_drafts_plan_revision_idx"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                ALTER TABLE {table}
                    ADD COLUMN IF NOT EXISTS plan_family_id UUID NULL,
                    ADD COLUMN IF NOT EXISTS latest_plan_revision_id UUID NULL,
                    ADD COLUMN IF NOT EXISTS plan_source_sha256 TEXT NULL,
                    ADD COLUMN IF NOT EXISTS draft_version BIGINT NOT NULL DEFAULT 1
                """
            ).format(table=sql.Identifier(table))
        )
        for constraint_name, definition in (
            (
                binding_check,
                sql.SQL(
                    """
                    CHECK (
                        (
                            plan_family_id IS NULL
                            AND latest_plan_revision_id IS NULL
                            AND plan_source_sha256 IS NULL
                        )
                        OR
                        (
                            plan_family_id IS NOT NULL
                            AND latest_plan_revision_id IS NOT NULL
                            AND plan_source_sha256 IS NOT NULL
                            AND plan_source_sha256 ~ '^[0-9a-f]{64}$'
                        )
                    )
                    """
                ),
            ),
            (version_check, sql.SQL("CHECK (draft_version > 0)")),
        ):
            cursor.execute(
                "SELECT 1 FROM pg_constraint WHERE conname=%s AND conrelid=%s::regclass",
                (constraint_name, table),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("ALTER TABLE {table} ADD CONSTRAINT {constraint} ").format(
                        table=sql.Identifier(table),
                        constraint=sql.Identifier(constraint_name),
                    )
                    + definition
                )
        cursor.execute("SELECT to_regclass(%s)", (revisions,))
        revision_relation = cursor.fetchone()
        if revision_relation is not None and revision_relation[0] is not None:
            cursor.execute(
                "SELECT 1 FROM pg_constraint WHERE conname=%s AND conrelid=%s::regclass",
                (revision_fk, table),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {table}
                        ADD CONSTRAINT {constraint}
                        FOREIGN KEY (latest_plan_revision_id)
                        REFERENCES {revisions} (plan_revision_id)
                        ON DELETE RESTRICT
                        """
                    ).format(
                        table=sql.Identifier(table),
                        constraint=sql.Identifier(revision_fk),
                        revisions=sql.Identifier(revisions),
                    )
                )
        cursor.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS {index}
                ON {table} (latest_plan_revision_id)
                WHERE deleted_at IS NULL
                  AND latest_plan_revision_id IS NOT NULL
                """
            ).format(
                index=sql.Identifier(revision_index),
                table=sql.Identifier(table),
            )
        )


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
        validate_runtime_table_prefix(table_prefix)
        self._provider = connection_provider
        self._table_prefix = table_prefix
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
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} (expires_at)"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self._table_prefix, "interview_drafts_expires_idx"
                            )
                        ),
                        table=sql.Identifier(self._table),
                    )
                )
            ensure_postgres_draft_plan_binding_schema(
                connection,
                table_prefix=self._table_prefix,
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
        plan_family_id: str | None = None,
        latest_plan_revision_id: str | None = None,
        plan_source_sha256: str | None = None,
        clear_plan: bool = False,
    ) -> dict[str, Any]:
        prepared = self.prepare_save(
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            title=title,
            draft_id=draft_id,
            plan_family_id=plan_family_id,
            latest_plan_revision_id=latest_plan_revision_id,
            plan_source_sha256=plan_source_sha256,
            clear_plan=clear_plan,
        )
        return self.commit_save(prepared)

    def prepare_save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
        plan_family_id: str | None = None,
        latest_plan_revision_id: str | None = None,
        plan_source_sha256: str | None = None,
        clear_plan: bool = False,
    ) -> dict[str, Any]:
        from psycopg2 import sql

        _validate_text(job_description, resume_text)
        resolved_id = draft_id or f"draft_{uuid4()}"
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT draft_id, job_description, resume_text, job_tags,
                               title, created_at, updated_at, expires_at,
                               plan_family_id::text,
                               latest_plan_revision_id::text,
                               plan_source_sha256, draft_version, deleted_at,
                               (deleted_at IS NULL AND expires_at > clock_timestamp())
                        FROM {table}
                        WHERE draft_id = %s
                        """
                    ).format(table=sql.Identifier(self._table)),
                    (resolved_id,),
                )
                existing = cursor.fetchone()
        active = bool(existing is not None and existing[13])
        if clear_plan:
            plan_family_id = None
            latest_plan_revision_id = None
            plan_source_sha256 = None
        elif plan_family_id is None and active:
            plan_family_id = existing[8]
            latest_plan_revision_id = existing[9]
            plan_source_sha256 = existing[10]
        validate_plan_binding(
            plan_family_id,
            latest_plan_revision_id,
            plan_source_sha256,
        )
        resolved_tags = list(job_tags or [])
        now = datetime.now(timezone.utc)
        current_version = int(existing[11]) if existing is not None else 0
        prepared = {
            "draft_id": resolved_id,
            "job_description": job_description,
            "resume_text": resume_text,
            "job_tags": resolved_tags,
            "title": title,
            "plan_family_id": plan_family_id,
            "latest_plan_revision_id": latest_plan_revision_id,
            "plan_source_sha256": plan_source_sha256,
            "plan_status": plan_status(
                current_source_sha256=self._plan_source_sha256(
                    job_description,
                    resume_text,
                    resolved_tags,
                ),
                plan_family_id=plan_family_id,
                plan_source_sha256=plan_source_sha256,
            ),
            "draft_version": current_version + 1,
            "durability": self.durability,
            "created_at": (
                existing[5].isoformat() if active else now.isoformat()
            ),
            "updated_at": now.isoformat(),
            "expires_at": (
                existing[7].isoformat()
                if active
                else (now + timedelta(seconds=self._ttl_seconds)).isoformat()
            ),
            "_expected_draft_version": current_version,
            "_expected_updated_at": existing[6] if existing is not None else None,
            "_expected_row_state": (
                "active" if active else "inactive" if existing else "missing"
            ),
        }
        return deepcopy(prepared)

    def commit_save(self, prepared: dict[str, Any]) -> dict[str, Any]:
        from psycopg2 import sql

        candidate = deepcopy(prepared)
        expected_version = int(candidate.pop("_expected_draft_version"))
        expected_updated_at = candidate.pop("_expected_updated_at")
        expected_state = candidate.pop("_expected_row_state")
        validate_plan_binding(
            candidate.get("plan_family_id"),
            candidate.get("latest_plan_revision_id"),
            candidate.get("plan_source_sha256"),
        )
        parameters = (
            candidate["draft_id"],
            candidate["job_description"],
            candidate["resume_text"],
            self._legacy_source_sha256(
                candidate["job_description"], candidate["resume_text"]
            ),
            json.dumps(candidate["job_tags"]),
            candidate.get("title"),
            candidate.get("plan_family_id"),
            candidate.get("latest_plan_revision_id"),
            candidate.get("plan_source_sha256"),
        )
        returning = sql.SQL(
            """
            RETURNING draft_id, job_description, resume_text, job_tags,
                      title, created_at, updated_at, expires_at,
                      plan_family_id::text, latest_plan_revision_id::text,
                      plan_source_sha256, draft_version
            """
        )
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                if expected_state == "missing":
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {table} (
                                draft_id, job_description, resume_text, source_sha256,
                                durability, job_tags, title,
                                plan_family_id, latest_plan_revision_id,
                                plan_source_sha256, draft_version,
                                created_at, updated_at, expires_at
                            ) VALUES (
                                %s, %s, %s, %s, 'postgres', %s::jsonb, %s,
                                %s::uuid, %s::uuid, %s, 1,
                                clock_timestamp(), clock_timestamp(),
                                clock_timestamp() + (%s * INTERVAL '1 second')
                            )
                            ON CONFLICT (draft_id) DO NOTHING
                            """
                        ).format(table=sql.Identifier(self._table))
                        + returning,
                        (*parameters, self._ttl_seconds),
                    )
                else:
                    revive = expected_state == "inactive"
                    lifecycle_set = (
                        sql.SQL(
                            "created_at=clock_timestamp(), "
                            "expires_at=clock_timestamp() + (%s * INTERVAL '1 second'), "
                        )
                        if revive
                        else sql.SQL("")
                    )
                    lifecycle_guard = (
                        sql.SQL(
                            "AND (deleted_at IS NOT NULL OR expires_at <= clock_timestamp())"
                        )
                        if revive
                        else sql.SQL(
                            "AND deleted_at IS NULL AND expires_at > clock_timestamp()"
                        )
                    )
                    query = (
                        sql.SQL(
                            """
                            UPDATE {table} SET
                                job_description=%s,
                                resume_text=%s,
                                source_sha256=%s,
                                job_tags=%s::jsonb,
                                title=%s,
                                plan_family_id=%s::uuid,
                                latest_plan_revision_id=%s::uuid,
                                plan_source_sha256=%s,
                                deleted_at=NULL,
                                updated_at=clock_timestamp(),
                                draft_version=draft_version + 1,
                            """
                        ).format(table=sql.Identifier(self._table))
                        + lifecycle_set
                        + sql.SQL(
                            """
                                durability='postgres'
                            WHERE draft_id=%s
                              AND draft_version=%s
                              AND updated_at=%s
                            """
                        )
                        + lifecycle_guard
                        + returning
                    )
                    update_parameters = (
                        candidate["job_description"],
                        candidate["resume_text"],
                        self._legacy_source_sha256(
                            candidate["job_description"], candidate["resume_text"]
                        ),
                        json.dumps(candidate["job_tags"]),
                        candidate.get("title"),
                        candidate.get("plan_family_id"),
                        candidate.get("latest_plan_revision_id"),
                        candidate.get("plan_source_sha256"),
                    )
                    if revive:
                        update_parameters += (self._ttl_seconds,)
                    update_parameters += (
                        candidate["draft_id"],
                        expected_version,
                        expected_updated_at,
                    )
                    cursor.execute(query, update_parameters)
                row = cursor.fetchone()
            if row is None:
                connection.rollback()
                raise DraftWriteConflict("draft changed after it was prepared")
            connection.commit()
        return self._row_payload(row)

    def get(self, draft_id: str) -> dict[str, Any]:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT draft_id, job_description, resume_text, job_tags,
                               title, created_at, updated_at, expires_at,
                               plan_family_id::text,
                               latest_plan_revision_id::text,
                               plan_source_sha256, draft_version
                        FROM {table}
                        WHERE draft_id = %s
                          AND expires_at > clock_timestamp()
                          AND deleted_at IS NULL
                        """
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
                        """
                        UPDATE {table}
                        SET deleted_at=clock_timestamp(),
                            updated_at=clock_timestamp(),
                            plan_family_id=NULL,
                            latest_plan_revision_id=NULL,
                            plan_source_sha256=NULL,
                            draft_version=draft_version + 1
                        WHERE draft_id = %s AND deleted_at IS NULL
                        """
                    ).format(table=sql.Identifier(self._table)),
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

    def plan_revision_bindings(self) -> dict[str, str]:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT draft_id, latest_plan_revision_id::text
                        FROM {table}
                        WHERE deleted_at IS NULL
                          AND expires_at > clock_timestamp()
                          AND latest_plan_revision_id IS NOT NULL
                        """
                    ).format(table=sql.Identifier(self._table))
                )
                rows = cursor.fetchall()
        return {str(draft_id): str(revision_id) for draft_id, revision_id in rows}

    @staticmethod
    def _legacy_source_sha256(job_description: str, resume_text: str) -> str:
        return sha256(
            json.dumps(
                {
                    "job_description": job_description,
                    "resume_text": resume_text,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _plan_source_sha256(
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> str:
        from app.services.interview_plan_revision import (
            PlanSourcePayload,
            source_payload_sha256,
        )

        return source_payload_sha256(
            PlanSourcePayload(
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
            )
        )

    def _row_payload(self, row) -> dict[str, Any]:
        if row is None:
            raise RuntimeError("draft write did not return a row")
        tags = list(row[3] or [])
        family_id = row[8]
        plan_source_sha256 = row[10]
        return {
            "draft_id": row[0],
            "job_description": row[1],
            "resume_text": row[2],
            "job_tags": tags,
            "title": row[4],
            "created_at": row[5].isoformat(),
            "updated_at": row[6].isoformat(),
            "expires_at": row[7].isoformat(),
            "plan_family_id": family_id,
            "latest_plan_revision_id": row[9],
            "plan_source_sha256": plan_source_sha256,
            "plan_status": plan_status(
                current_source_sha256=self._plan_source_sha256(
                    row[1],
                    row[2],
                    tags,
                ),
                plan_family_id=family_id,
                plan_source_sha256=plan_source_sha256,
            ),
            "draft_version": int(row[11]),
            "durability": self.durability,
        }
