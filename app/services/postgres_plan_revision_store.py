from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.interview_plan_audit import PlanRevisionAudit
from app.services.interview_plan_revision import (
    InterviewPlanRevision,
    InterviewPlanV2,
    PlanCreatedReason,
    PlanRevisionSourceKind,
    PlanSourcePayload,
    PlanSourceRecord,
    PlanSourceReference,
    PlanSourceReferenceType,
    plan_payload_sha256,
    source_payload_sha256,
    utc_now,
)
from app.services.interview_plan_revision_store import (
    PlanRevisionConflict,
    PlanRevisionNotFound,
    PlanSourceInUse,
    PlanSourceUnavailable,
    _default_revision_audit,
    _validate_request_identity,
)
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations


class PostgresInterviewPlanRevisionStore:
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
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.sources_table = f"{table_prefix}_plan_sources"
        self.source_refs_table = f"{table_prefix}_plan_source_refs"
        self.revisions_table = f"{table_prefix}_plan_revisions"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (self.sources_table, self.source_refs_table, self.revisions_table),
            )

    def create_initial(
        self,
        *,
        source_payload: PlanSourcePayload,
        plan: InterviewPlanV2,
        retention_policy: str,
        generator_version: str,
        plan_family_id: str | None = None,
    ) -> InterviewPlanRevision:
        family_id = str(plan_family_id or uuid4())
        source_id = str(uuid4())
        revision_id = str(uuid4())
        now = utc_now()
        source = PlanSourceRecord(
            source_id=source_id,
            plan_family_id=family_id,
            source_sha256=source_payload_sha256(source_payload),
            protected_payload=source_payload,
            retention_policy=retention_policy,
            created_at=now,
        )
        revision = self._make_revision(
            plan_revision_id=revision_id,
            plan_family_id=family_id,
            revision=1,
            parent_revision_id=None,
            source_kind="generated",
            source=source,
            plan=plan,
            generator_version=generator_version,
            created_reason="initial_generation",
            created_at=now,
        )
        _, sql, extras = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {sources} (
                            source_id, plan_family_id, source_sha256,
                            protected_payload, retention_policy, created_at
                        ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
                        ON CONFLICT (plan_family_id) DO NOTHING
                        RETURNING source_id::text
                        """
                    ).format(sources=sql.Identifier(self.sources_table)),
                    (
                        source.source_id,
                        source.plan_family_id,
                        source.source_sha256,
                        extras.Json(source_payload.model_dump(mode="json")),
                        source.retention_policy,
                        source.created_at,
                    ),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL(
                            "SELECT MAX(revision) FROM {revisions} "
                            "WHERE plan_family_id = %s::uuid"
                        ).format(revisions=sql.Identifier(self.revisions_table)),
                        (family_id,),
                    )
                    current = cursor.fetchone()[0]
                    raise PlanRevisionConflict(
                        "plan family already exists", current_revision=current
                    )
                self._insert_revision(cursor, revision, extras=extras)
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {refs} (
                            source_id, owner_type, owner_id, created_at
                        ) VALUES (%s::uuid, 'family', %s, %s)
                        """
                    ).format(refs=sql.Identifier(self.source_refs_table)),
                    (source.source_id, family_id, now),
                )
        return revision

    def create_next_revision(
        self,
        *,
        plan_family_id: str,
        expected_revision: int,
        plan: InterviewPlanV2,
        source_kind: PlanRevisionSourceKind,
        created_reason: PlanCreatedReason,
        generator_version: str,
        request_id: str | None = None,
        request_sha256: str | None = None,
        audit: PlanRevisionAudit | None = None,
    ) -> InterviewPlanRevision:
        _validate_request_identity(request_id, request_sha256)
        _, sql, extras = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(self._select_source_sql("FOR UPDATE")).format(
                        sources=sql.Identifier(self.sources_table)
                    ),
                    (plan_family_id,),
                )
                source_row = cursor.fetchone()
                if source_row is None:
                    raise PlanRevisionNotFound("plan family not found")
                source = self._source_from_row(source_row)
                if request_id is not None:
                    cursor.execute(
                        sql.SQL(self._select_revision_sql(
                            "WHERE plan_family_id = %s::uuid AND request_id = %s"
                        )).format(
                            revisions=sql.Identifier(self.revisions_table)
                        ),
                        (plan_family_id, request_id),
                    )
                    request_row = cursor.fetchone()
                    if request_row is not None:
                        if request_row[15] != request_sha256:
                            raise PlanRevisionConflict("request ID payload conflicts")
                        return self._revision_from_row(request_row)
                cursor.execute(
                    sql.SQL(self._select_latest_revision_sql("FOR UPDATE")).format(
                        revisions=sql.Identifier(self.revisions_table)
                    ),
                    (plan_family_id,),
                )
                current_row = cursor.fetchone()
                if current_row is None:
                    raise PlanRevisionNotFound("plan family has no revision")
                current = self._revision_from_row(current_row)
                if current.revision != expected_revision:
                    raise PlanRevisionConflict(
                        "expected revision does not match latest revision",
                        current_revision=current.revision,
                    )
                if source.protected_payload is None and created_reason in {
                    "regenerate_question",
                    "regenerate_all",
                }:
                    raise PlanSourceUnavailable("plan source payload is unavailable")
                if (
                    audit is not None
                    and audit.parent_plan_sha256 != current.plan_sha256
                ):
                    raise ValueError(
                        "revision audit parent hash does not match current revision"
                    )
                revision = self._make_revision(
                    plan_revision_id=str(uuid4()),
                    plan_family_id=plan_family_id,
                    revision=current.revision + 1,
                    parent_revision_id=current.plan_revision_id,
                    source_kind=source_kind,
                    source=source,
                    plan=plan,
                    generator_version=generator_version,
                    created_reason=created_reason,
                    created_at=utc_now(),
                    audit=(
                        audit
                        or _default_revision_audit(
                            created_reason=created_reason,
                            source_sha256=source.source_sha256,
                            parent_plan_sha256=current.plan_sha256,
                            result_plan_sha256=plan_payload_sha256(plan),
                        )
                    ),
                )
                self._insert_revision(
                    cursor,
                    revision,
                    extras=extras,
                    request_id=request_id,
                    request_sha256=request_sha256,
                )
                return revision

    def get_by_id(self, plan_revision_id: str) -> InterviewPlanRevision:
        _, sql, _ = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(self._select_revision_sql("WHERE plan_revision_id = %s::uuid")).format(
                        revisions=sql.Identifier(self.revisions_table)
                    ),
                    (plan_revision_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise PlanRevisionNotFound("plan revision not found")
        return self._revision_from_row(row)

    def get_latest(self, plan_family_id: str) -> InterviewPlanRevision:
        _, sql, _ = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(self._select_latest_revision_sql("")).format(
                        revisions=sql.Identifier(self.revisions_table)
                    ),
                    (plan_family_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise PlanRevisionNotFound("plan family not found")
        return self._revision_from_row(row)

    def list_revisions(self, plan_family_id: str) -> list[InterviewPlanRevision]:
        _, sql, _ = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        self._select_revision_sql(
                            "WHERE plan_family_id = %s::uuid ORDER BY revision"
                        )
                    ).format(revisions=sql.Identifier(self.revisions_table)),
                    (plan_family_id,),
                )
                rows = cursor.fetchall()
        if not rows:
            raise PlanRevisionNotFound("plan family not found")
        return [self._revision_from_row(row) for row in rows]

    def get_source(self, source_id: str) -> PlanSourceRecord:
        _, sql, _ = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(self._select_source_by_id_sql("")).format(
                        sources=sql.Identifier(self.sources_table)
                    ),
                    (source_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise PlanRevisionNotFound("plan source not found")
        return self._source_from_row(row)

    def list_source_references(self, source_id: str) -> list[PlanSourceReference]:
        _, sql, _ = self._imports()
        self.get_source(source_id)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT source_id::text, owner_type, owner_id, created_at "
                        "FROM {refs} WHERE source_id = %s::uuid "
                        "ORDER BY owner_type, owner_id"
                    ).format(refs=sql.Identifier(self.source_refs_table)),
                    (source_id,),
                )
                rows = cursor.fetchall()
        return [self._reference_from_row(row) for row in rows]

    def add_source_reference(
        self,
        source_id: str,
        *,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> PlanSourceReference:
        _, sql, _ = self._imports()
        now = utc_now()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {refs} (source_id, owner_type, owner_id, created_at)
                        VALUES (%s::uuid, %s, %s, %s)
                        ON CONFLICT (source_id, owner_type, owner_id)
                        DO UPDATE SET owner_id = EXCLUDED.owner_id
                        RETURNING source_id::text, owner_type, owner_id, created_at
                        """
                    ).format(refs=sql.Identifier(self.source_refs_table)),
                    (source_id, owner_type, owner_id, now),
                )
                row = cursor.fetchone()
        return self._reference_from_row(row)

    def remove_source_reference(
        self,
        source_id: str,
        *,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> bool:
        _, sql, _ = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {refs} WHERE source_id = %s::uuid "
                        "AND owner_type = %s AND owner_id = %s"
                    ).format(refs=sql.Identifier(self.source_refs_table)),
                    (source_id, owner_type, owner_id),
                )
                return cursor.rowcount == 1

    def tombstone_source_payload(self, source_id: str, *, reason: str) -> PlanSourceRecord:
        if not reason.strip():
            raise ValueError("tombstone reason is required")
        _, sql, _ = self._imports()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(self._select_source_by_id_sql("FOR UPDATE")).format(
                        sources=sql.Identifier(self.sources_table)
                    ),
                    (source_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PlanRevisionNotFound("plan source not found")
                source = self._source_from_row(row)
                cursor.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FROM {refs} WHERE source_id = %s::uuid"
                    ).format(refs=sql.Identifier(self.source_refs_table)),
                    (source_id,),
                )
                if cursor.fetchone()[0] != 0:
                    raise PlanSourceInUse("plan source still has active references")
                if source.protected_payload is None:
                    return source
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {sources}
                        SET protected_payload = NULL,
                            tombstoned_at = NOW(),
                            tombstone_reason = %s
                        WHERE source_id = %s::uuid
                        RETURNING source_id::text, plan_family_id::text,
                                  source_sha256, protected_payload,
                                  retention_policy, created_at,
                                  tombstoned_at, tombstone_reason
                        """
                    ).format(sources=sql.Identifier(self.sources_table)),
                    (reason.strip(), source_id),
                )
                return self._source_from_row(cursor.fetchone())

    def _ensure_schema(self) -> None:
        _, sql, _ = self._imports()
        sources = sql.Identifier(self.sources_table)
        refs = sql.Identifier(self.source_refs_table)
        revisions = sql.Identifier(self.revisions_table)
        function_name = runtime_schema_identifier(
            self.table_prefix, "reject_plan_revision_update"
        )
        trigger_name = runtime_schema_identifier(
            self.table_prefix, "plan_revisions_immutable_trigger"
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {sources} (
                            source_id UUID PRIMARY KEY,
                            plan_family_id UUID NOT NULL UNIQUE,
                            source_sha256 TEXT NOT NULL CHECK (
                                source_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            protected_payload JSONB,
                            retention_policy TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            tombstoned_at TIMESTAMPTZ,
                            tombstone_reason TEXT,
                            CHECK (
                                (protected_payload IS NOT NULL
                                 AND tombstoned_at IS NULL
                                 AND tombstone_reason IS NULL)
                                OR
                                (protected_payload IS NULL
                                 AND tombstoned_at IS NOT NULL
                                 AND tombstone_reason IS NOT NULL)
                            )
                        )
                        """
                    ).format(sources=sources)
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {revisions} (
                            plan_revision_id UUID PRIMARY KEY,
                            plan_family_id UUID NOT NULL,
                            revision INTEGER NOT NULL CHECK (revision >= 1),
                            parent_revision_id UUID REFERENCES {revisions}(plan_revision_id),
                            source_kind TEXT NOT NULL CHECK (
                                source_kind IN (
                                    'generated', 'edited',
                                    'regenerated_question', 'customized'
                                )
                            ),
                            source_id UUID NOT NULL REFERENCES {sources}(source_id),
                            source_sha256 TEXT NOT NULL CHECK (
                                source_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            configuration_snapshot_json JSONB NOT NULL,
                            plan_json JSONB NOT NULL,
                            plan_sha256 TEXT NOT NULL CHECK (
                                plan_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            generator_version TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            created_reason TEXT NOT NULL,
                            audit_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            request_id TEXT,
                            request_sha256 TEXT CHECK (
                                request_sha256 IS NULL OR
                                request_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            UNIQUE (plan_family_id, revision),
                            CHECK (
                                (request_id IS NULL AND request_sha256 IS NULL)
                                OR (request_id IS NOT NULL AND request_sha256 IS NOT NULL)
                            ),
                            CHECK (
                                (revision = 1 AND parent_revision_id IS NULL)
                                OR (revision > 1 AND parent_revision_id IS NOT NULL)
                            )
                        )
                        """
                    ).format(revisions=revisions, sources=sources)
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {revisions} ADD COLUMN IF NOT EXISTS "
                        "audit_json JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                    ).format(revisions=revisions)
                )
                cursor.execute(
                    sql.SQL("ALTER TABLE {revisions} ADD COLUMN IF NOT EXISTS request_id TEXT").format(revisions=revisions)
                )
                cursor.execute(
                    sql.SQL("ALTER TABLE {revisions} ADD COLUMN IF NOT EXISTS request_sha256 TEXT").format(revisions=revisions)
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {refs} (
                            source_id UUID NOT NULL REFERENCES {sources}(source_id),
                            owner_type TEXT NOT NULL CHECK (
                                owner_type IN ('family', 'draft', 'session')
                            ),
                            owner_id TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            PRIMARY KEY (source_id, owner_type, owner_id)
                        )
                        """
                    ).format(refs=refs, sources=sources)
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {revisions} "
                        "(plan_family_id, request_id) WHERE request_id IS NOT NULL"
                    ).format(
                        index=sql.Identifier(runtime_schema_identifier(
                            self.table_prefix, "plan_revisions_request_uq"
                        )),
                        revisions=revisions,
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {revisions} "
                        "(plan_family_id, revision DESC)"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix, "plan_revisions_family_revision_idx"
                            )
                        ),
                        revisions=revisions,
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {refs} "
                        "(owner_type, owner_id)"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix, "plan_source_refs_owner_idx"
                            )
                        ),
                        refs=refs,
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE OR REPLACE FUNCTION {function}()
                        RETURNS TRIGGER LANGUAGE plpgsql AS $body$
                        BEGIN
                            RAISE EXCEPTION 'plan revisions are immutable'
                                USING ERRCODE = '55000';
                        END
                        $body$
                        """
                    ).format(function=sql.Identifier(function_name))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        DO $body$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM pg_trigger
                                WHERE tgname = {trigger_literal}
                                  AND tgrelid = {table_literal}::regclass
                            ) THEN
                                CREATE TRIGGER {trigger}
                                BEFORE UPDATE ON {revisions}
                                FOR EACH ROW EXECUTE FUNCTION {function}();
                            END IF;
                        END
                        $body$
                        """
                    ).format(
                        trigger_literal=sql.Literal(trigger_name),
                        table_literal=sql.Literal(self.revisions_table),
                        trigger=sql.Identifier(trigger_name),
                        revisions=revisions,
                        function=sql.Identifier(function_name),
                    )
                )

    def _insert_revision(
        self,
        cursor,
        revision: InterviewPlanRevision,
        *,
        extras,
        request_id: str | None = None,
        request_sha256: str | None = None,
    ) -> None:
        cursor.execute(
            self._sql(
                """
                INSERT INTO {revisions} (
                    plan_revision_id, plan_family_id, revision,
                    parent_revision_id, source_kind, source_id, source_sha256,
                    configuration_snapshot_json, plan_json, plan_sha256,
                    generator_version, created_at, created_reason,
                    audit_json, request_id, request_sha256
                ) VALUES (
                    %s::uuid, %s::uuid, %s, %s::uuid, %s, %s::uuid, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """
            ),
            (
                revision.plan_revision_id,
                revision.plan_family_id,
                revision.revision,
                revision.parent_revision_id,
                revision.source_kind,
                revision.source_id,
                revision.source_sha256,
                extras.Json(revision.configuration_snapshot.model_dump(mode="json")),
                extras.Json(revision.plan.model_dump(mode="json")),
                revision.plan_sha256,
                revision.generator_version,
                revision.created_at,
                revision.created_reason,
                extras.Json(revision.audit.model_dump(mode="json")),
                request_id,
                request_sha256,
            ),
        )

    def _make_revision(self, **kwargs) -> InterviewPlanRevision:
        plan = kwargs["plan"]
        source = kwargs["source"]
        result_plan_sha256 = plan_payload_sha256(plan)
        audit = kwargs.get("audit") or _default_revision_audit(
            created_reason=kwargs["created_reason"],
            source_sha256=source.source_sha256,
            parent_plan_sha256=None,
            result_plan_sha256=result_plan_sha256,
        )
        return InterviewPlanRevision(
            plan_revision_id=kwargs["plan_revision_id"],
            plan_family_id=kwargs["plan_family_id"],
            revision=kwargs["revision"],
            parent_revision_id=kwargs["parent_revision_id"],
            source_kind=kwargs["source_kind"],
            source_id=source.source_id,
            source_sha256=source.source_sha256,
            configuration_snapshot=plan.configuration_snapshot,
            plan=plan,
            plan_sha256=result_plan_sha256,
            generator_version=kwargs["generator_version"],
            created_at=kwargs["created_at"],
            created_reason=kwargs["created_reason"],
            audit=audit,
        )

    def _select_revision_sql(self, suffix: str) -> str:
        return (
            "SELECT plan_revision_id::text, plan_family_id::text, revision, "
            "parent_revision_id::text, source_kind, source_id::text, "
            "source_sha256, configuration_snapshot_json, plan_json, "
            "plan_sha256, generator_version, created_at, created_reason, "
            "audit_json, request_id, request_sha256 "
            "FROM {revisions} " + suffix
        )

    def _select_latest_revision_sql(self, lock_suffix: str) -> str:
        return self._select_revision_sql(
            "WHERE plan_family_id = %s::uuid ORDER BY revision DESC LIMIT 1 "
            + lock_suffix
        )

    @staticmethod
    def _select_source_sql(lock_suffix: str) -> str:
        return (
            "SELECT source_id::text, plan_family_id::text, source_sha256, "
            "protected_payload, retention_policy, created_at, "
            "tombstoned_at, tombstone_reason FROM {sources} "
            "WHERE plan_family_id = %s::uuid " + lock_suffix
        )

    @staticmethod
    def _select_source_by_id_sql(lock_suffix: str) -> str:
        return (
            "SELECT source_id::text, plan_family_id::text, source_sha256, "
            "protected_payload, retention_policy, created_at, "
            "tombstoned_at, tombstone_reason FROM {sources} "
            "WHERE source_id = %s::uuid " + lock_suffix
        )

    @staticmethod
    def _revision_from_row(row) -> InterviewPlanRevision:
        audit = row[13]
        if not audit:
            audit = _default_revision_audit(
                created_reason=row[12],
                source_sha256=row[6],
                parent_plan_sha256=None,
                result_plan_sha256=row[9],
            )
        return InterviewPlanRevision(
            plan_revision_id=row[0],
            plan_family_id=row[1],
            revision=row[2],
            parent_revision_id=row[3],
            source_kind=row[4],
            source_id=row[5],
            source_sha256=row[6],
            configuration_snapshot=row[7],
            plan=row[8],
            plan_sha256=row[9],
            generator_version=row[10],
            created_at=row[11],
            created_reason=row[12],
            audit=audit,
        )

    @staticmethod
    def _source_from_row(row) -> PlanSourceRecord:
        return PlanSourceRecord(
            source_id=row[0],
            plan_family_id=row[1],
            source_sha256=row[2],
            protected_payload=row[3],
            retention_policy=row[4],
            created_at=row[5],
            tombstoned_at=row[6],
            tombstone_reason=row[7],
        )

    @staticmethod
    def _reference_from_row(row) -> PlanSourceReference:
        return PlanSourceReference(
            source_id=row[0], owner_type=row[1], owner_id=row[2], created_at=row[3]
        )

    def _sql(self, statement: str):
        _, sql, _ = self._imports()
        return sql.SQL(statement).format(
            sources=sql.Identifier(self.sources_table),
            refs=sql.Identifier(self.source_refs_table),
            revisions=sql.Identifier(self.revisions_table),
        )

    @staticmethod
    def _imports() -> tuple[Any, Any, Any]:
        import psycopg2
        from psycopg2 import extras, sql

        return psycopg2, sql, extras
