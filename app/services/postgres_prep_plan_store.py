from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import runtime_schema_identifier, validate_postgres_identifier
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.prep import InterviewPlan
from app.services.prep_plans import (
    PrepPlanError,
    apply_plan_operations,
    build_regenerated_state,
    build_prep_plan_record,
    plan_expired,
    plan_not_found,
    public_from_record,
    regeneration_context_from_record,
    version_snapshot,
)


class PostgresPrepPlanStore:
    durability = "postgres"

    def __init__(
        self,
        dsn: str | None = None,
        *,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
        ttl: timedelta = timedelta(hours=24),
        expired_grace: timedelta = timedelta(hours=24),
        consumed_retention: timedelta = timedelta(days=7),
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("prep plan ttl must be positive")
        if expired_grace.total_seconds() <= 0:
            raise ValueError("prep plan expired grace must be positive")
        if consumed_retention.total_seconds() <= 0:
            raise ValueError("prep plan consumed retention must be positive")
        owned = connection_provider is None
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
        validate_postgres_identifier(table_prefix)
        self._provider = connection_provider
        self._plans_table = f"{table_prefix}_prep_plans"
        self._versions_table = f"{table_prefix}_prep_plan_versions"
        self._drafts_table = f"{table_prefix}_interview_drafts"
        self._ttl = ttl
        self._expired_grace_seconds = int(expired_grace.total_seconds())
        self._consumed_retention_seconds = int(consumed_retention.total_seconds())
        mode = resolve_schema_mode(schema_mode, provider_is_owned=owned)
        if mode == "migrate":
            self.ensure_schema()
        else:
            validate_relations(
                self._provider,
                (self._plans_table, self._versions_table),
            )

    @property
    def connection_provider(self) -> ConnectionProvider:
        return self._provider

    def ensure_schema(self) -> None:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {plans} (
                            plan_id TEXT PRIMARY KEY,
                            plan_version INTEGER NOT NULL CHECK (plan_version >= 1),
                            state TEXT NOT NULL CHECK (state IN ('editable', 'consumed', 'expired')),
                            plan_json JSONB NOT NULL,
                            internal_context_json JSONB NOT NULL,
                            source_sha256 TEXT NOT NULL,
                            source_draft_id TEXT NULL,
                            expires_at TIMESTAMPTZ NOT NULL,
                            consumed_session_id TEXT NULL,
                            consumed_command_id TEXT NULL,
                            consumed_plan_version INTEGER NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            FOREIGN KEY (source_draft_id) REFERENCES {drafts}(draft_id) ON DELETE SET NULL
                        )
                        """
                    ).format(
                        plans=sql.Identifier(self._plans_table),
                        drafts=sql.Identifier(self._drafts_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {versions} (
                            plan_id TEXT NOT NULL,
                            version INTEGER NOT NULL CHECK (version >= 1),
                            public_snapshot_json JSONB NOT NULL,
                            change_type TEXT NOT NULL,
                            replaced_question_id TEXT NULL,
                            replacement_question_id TEXT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (plan_id, version),
                            FOREIGN KEY (plan_id) REFERENCES {plans}(plan_id) ON DELETE CASCADE
                        )
                        """
                    ).format(
                        versions=sql.Identifier(self._versions_table),
                        plans=sql.Identifier(self._plans_table),
                    )
                )
                cursor.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {plans} (state, expires_at)").format(
                        index=sql.Identifier(runtime_schema_identifier(self._plans_table.removesuffix("_prep_plans"), "prep_plans_state_expiry_idx")),
                        plans=sql.Identifier(self._plans_table),
                    )
                )
            if not hasattr(self._provider, "connection_object"):
                connection.commit()

    def create(
        self,
        *,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        source_draft_id: str | None = None,
        practice_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        record = build_prep_plan_record(
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            durability=self.durability,
            created_at=now,
            expires_at=now + self._ttl,
            source_draft_id=source_draft_id,
            practice_provenance=practice_provenance,
        )
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                self._insert_record(cursor, record)
            connection.commit()
        return public_from_record(record)

    def get(self, plan_id: str) -> dict[str, Any]:
        self.cleanup()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                record = self.select_locked(cursor, plan_id, for_update=False)
        self._assert_available(record, plan_id)
        return public_from_record(record)

    def apply_operations(
        self,
        plan_id: str,
        *,
        expected_version: int,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from psycopg2 import sql

        self.cleanup()

        with self._provider.connection() as connection:
            try:
                with connection.cursor() as cursor:
                    record = self.select_locked(cursor, plan_id, for_update=True)
                    self._assert_available(record, plan_id)
                    self._assert_editable(record)
                    next_public = apply_plan_operations(
                        record["public"],
                        expected_version=expected_version,
                        operations=operations,
                    )
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {plans} SET plan_version=%s, plan_json=%s::jsonb, "
                            "updated_at=NOW() WHERE plan_id=%s AND plan_version=%s"
                        ).format(plans=sql.Identifier(self._plans_table)),
                        (
                            next_public["plan_version"],
                            json.dumps(next_public, ensure_ascii=False),
                            plan_id,
                            expected_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise PrepPlanError(
                            "PREP_PLAN_VERSION_CONFLICT",
                            "计划已经更新，请确认最新版本。",
                            status_code=409,
                            retryable=True,
                        )
                    self._insert_version(cursor, version_snapshot(next_public, change_type="patched"))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return next_public

    def get_regeneration_context(
        self,
        plan_id: str,
        *,
        question_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        self.cleanup()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                record = self.select_locked(cursor, plan_id, for_update=False)
        self._assert_available(record, plan_id)
        self._assert_editable(record)
        return regeneration_context_from_record(
            record,
            expected_version=expected_version,
            question_id=question_id,
        )

    def replace_question(
        self,
        plan_id: str,
        *,
        question_id: str,
        expected_version: int,
        replacement: dict[str, Any],
    ) -> dict[str, Any]:
        from psycopg2 import sql

        self.cleanup()
        with self._provider.connection() as connection:
            try:
                with connection.cursor() as cursor:
                    record = self.select_locked(cursor, plan_id, for_update=True)
                    self._assert_available(record, plan_id)
                    self._assert_editable(record)
                    next_public, contexts, catalog = build_regenerated_state(
                        record,
                        expected_version=expected_version,
                        replaced_question_id=question_id,
                        replacement=replacement,
                    )
                    replacement_id = replacement["public_question"]["question_id"]
                    record["question_contexts"] = contexts
                    record["context_catalog"] = catalog
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {plans} SET plan_version=%s, plan_json=%s::jsonb, "
                            "internal_context_json=%s::jsonb, updated_at=NOW() "
                            "WHERE plan_id=%s AND plan_version=%s"
                        ).format(plans=sql.Identifier(self._plans_table)),
                        (
                            next_public["plan_version"],
                            json.dumps(next_public, ensure_ascii=False),
                            json.dumps(self._internal_payload(record), ensure_ascii=False),
                            plan_id,
                            expected_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise PrepPlanError(
                            "PREP_PLAN_VERSION_CONFLICT",
                            "计划已经更新，请确认最新版本。",
                            status_code=409,
                            retryable=True,
                        )
                    self._insert_version(
                        cursor,
                        version_snapshot(
                            next_public,
                            change_type="regenerated",
                            replaced_question_id=question_id,
                            replacement_question_id=replacement_id,
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        response = deepcopy(next_public)
        response["replaced_question_id"] = question_id
        response["replacement_question_id"] = replacement_id
        return response

    def delete_by_source_draft(self, draft_id: str) -> int:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {plans} WHERE source_draft_id=%s AND state <> 'consumed'"
                    ).format(plans=sql.Identifier(self._plans_table)),
                    (draft_id,),
                )
                deleted = cursor.rowcount
            connection.commit()
        return deleted

    def cleanup(self) -> int:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {plans} SET state='expired', updated_at=NOW() "
                            "WHERE state='editable' AND expires_at <= NOW()"
                        ).format(plans=sql.Identifier(self._plans_table))
                    )
                    cursor.execute(
                        sql.SQL(
                            "DELETE FROM {plans} WHERE "
                            "(state='expired' AND expires_at + (%s * INTERVAL '1 second') <= NOW()) "
                            "OR (state='consumed' AND updated_at + (%s * INTERVAL '1 second') <= NOW())"
                        ).format(plans=sql.Identifier(self._plans_table)),
                        (
                            self._expired_grace_seconds,
                            self._consumed_retention_seconds,
                        ),
                    )
                    removed = cursor.rowcount
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return removed

    def select_locked(self, cursor, plan_id: str, *, for_update: bool = True) -> dict[str, Any]:
        from psycopg2 import sql

        suffix = sql.SQL(" FOR UPDATE") if for_update else sql.SQL("")
        cursor.execute(
            sql.SQL(
                "SELECT plan_id, plan_version, state, plan_json, internal_context_json, "
                "source_sha256, source_draft_id, expires_at, consumed_session_id, "
                "consumed_command_id, consumed_plan_version, created_at, updated_at "
                "FROM {plans} WHERE plan_id=%s"
            ).format(plans=sql.Identifier(self._plans_table)) + suffix,
            (plan_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise plan_not_found(plan_id)
        return self._row_record(row)

    def mark_consumed(
        self,
        cursor,
        *,
        plan_id: str,
        session_id: str,
        command_id: str,
        consumed_plan_version: int,
    ) -> None:
        from psycopg2 import sql

        cursor.execute(
            sql.SQL(
                "UPDATE {plans} SET state='consumed', consumed_session_id=%s, "
                "consumed_command_id=%s, consumed_plan_version=%s, updated_at=NOW() "
                "WHERE plan_id=%s AND state='editable'"
            ).format(plans=sql.Identifier(self._plans_table)),
            (session_id, command_id, consumed_plan_version, plan_id),
        )
        if cursor.rowcount != 1:
            raise PrepPlanError(
                "PREP_PLAN_ALREADY_CONSUMED",
                "计划已经用于创建面试。",
                status_code=409,
            )

    def _insert_record(self, cursor, record: dict[str, Any]) -> None:
        from psycopg2 import sql

        public = record["public"]
        internal = self._internal_payload(record)
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {plans} (
                    plan_id, plan_version, state, plan_json, internal_context_json,
                    source_sha256, source_draft_id, expires_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
                """
            ).format(plans=sql.Identifier(self._plans_table)),
            (
                public["plan_id"],
                public["plan_version"],
                record["state"],
                json.dumps(public, ensure_ascii=False),
                json.dumps(internal, ensure_ascii=False),
                record["source_sha256"],
                record["source_draft_id"],
                record["expires_at"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        self._insert_version(cursor, record["versions"][1])

    def _insert_version(self, cursor, snapshot: dict[str, Any]) -> None:
        from psycopg2 import sql

        cursor.execute(
            sql.SQL(
                "INSERT INTO {versions} (plan_id, version, public_snapshot_json, "
                "change_type, replaced_question_id, replacement_question_id) "
                "VALUES (%s, %s, %s::jsonb, %s, %s, %s)"
            ).format(versions=sql.Identifier(self._versions_table)),
            (
                snapshot["plan_id"],
                snapshot["version"],
                json.dumps(snapshot["public_snapshot"], ensure_ascii=False),
                snapshot["change_type"],
                snapshot["replaced_question_id"],
                snapshot["replacement_question_id"],
            ),
        )

    def _row_record(self, row) -> dict[str, Any]:
        public = dict(row[3])
        internal = dict(row[4])
        return {
            "public": public,
            "internal_plan": internal["internal_plan"],
            "question_contexts": dict(internal.get("question_contexts") or {}),
            "context_catalog": dict(internal.get("context_catalog") or {}),
            "job_description": internal["job_description"],
            "resume_text": internal["resume_text"],
            "job_tags": list(internal.get("job_tags") or []),
            "practice_provenance": deepcopy(
                internal.get("practice_provenance")
            ),
            "source_sha256": row[5],
            "source_draft_id": row[6],
            "expires_at": row[7].isoformat(),
            "state": row[2],
            "consumed_session_id": row[8],
            "consumed_command_id": row[9],
            "consumed_plan_version": row[10],
            "created_at": row[11].isoformat(),
            "updated_at": row[12].isoformat(),
        }

    @staticmethod
    def _internal_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "internal_plan": record["internal_plan"],
            "question_contexts": record.get("question_contexts") or {},
            "context_catalog": record.get("context_catalog") or {},
            "job_description": record["job_description"],
            "resume_text": record["resume_text"],
            "job_tags": record["job_tags"],
            "practice_provenance": record.get("practice_provenance"),
        }

    @staticmethod
    def _assert_available(record: dict[str, Any], plan_id: str) -> None:
        expires_at = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if record["state"] == "expired" or expires_at <= datetime.now(timezone.utc):
            raise plan_expired(plan_id)

    @staticmethod
    def _assert_editable(record: dict[str, Any]) -> None:
        if record["state"] != "editable":
            raise PrepPlanError(
                "PREP_PLAN_ALREADY_CONSUMED",
                "计划已用于创建面试，不能继续编辑。",
                status_code=409,
                details={"session_id": record.get("consumed_session_id")},
            )
