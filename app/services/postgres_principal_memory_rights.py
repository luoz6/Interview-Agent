from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.principal_memory_rights import (
    PrincipalMemoryDeletionTombstone,
    PrincipalMemoryExportRecord,
    _tombstone_digest,
)
from app.services.principal_memory_safe_refs import (
    PrincipalMemorySafeRefInvalid,
    PrincipalMemorySafeRefRecord,
)


class _PostgresPrincipalMemoryStore:
    def __init__(
        self,
        *,
        dsn: str | None,
        connection_provider: ConnectionProvider | None,
        table_prefix: str,
        schema_mode: str | None,
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
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=provider_is_owned,
        )


class PostgresPrincipalMemoryExportStore(_PostgresPrincipalMemoryStore):
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        super().__init__(
            dsn=dsn,
            connection_provider=connection_provider,
            table_prefix=table_prefix,
            schema_mode=schema_mode,
        )
        self.table = f"{table_prefix}_principal_memory_exports"
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def put(self, record: PrincipalMemoryExportRecord):
        from psycopg2 import sql
        import json

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            schema_version,export_ref,deployment_id,principal_id,
                            payload,created_at,expires_at
                        ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)
                        ON CONFLICT (export_ref) DO NOTHING
                        RETURNING schema_version,export_ref,deployment_id,
                            principal_id,payload,created_at,expires_at
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        record.schema_version,
                        record.export_ref,
                        record.deployment_id,
                        record.principal_id,
                        json.dumps(
                            record.payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        record.created_at,
                        record.expires_at,
                    ),
                )
                row = cursor.fetchone()
        if row is not None:
            return self._from_row(row)
        current = self._get_unbounded(record.export_ref)
        if current != record:
            raise RuntimeError("principal memory export reference collision")
        return current

    def get(self, export_ref: str, *, now):
        record = self._get_unbounded(export_ref)
        return record if record is not None and record.expires_at > now else None

    def _get_unbounded(self, export_ref):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT schema_version,export_ref,deployment_id,"
                        "principal_id,payload,created_at,expires_at FROM {table} "
                        "WHERE export_ref=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (export_ref,),
                )
                row = cursor.fetchone()
        return self._from_row(row) if row else None

    def purge(self, *, deployment_id: str, principal_id: str) -> int:
        return self._delete_or_count(
            deployment_id=deployment_id,
            principal_id=principal_id,
            delete=True,
        )

    def count(self, *, deployment_id: str, principal_id: str) -> int:
        return self._delete_or_count(
            deployment_id=deployment_id,
            principal_id=principal_id,
            delete=False,
        )

    def cleanup_expired(self, *, now, batch_size: int = 200) -> int:
        if now.tzinfo is None:
            raise ValueError("principal memory cleanup time must be timezone-aware")
        if batch_size < 1:
            raise ValueError("principal memory cleanup batch size must be positive")
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {table} WHERE ctid IN ("
                        "SELECT ctid FROM {table} WHERE expires_at<=%s "
                        "ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED)"
                    ).format(table=sql.Identifier(self.table)),
                    (now, batch_size),
                )
                return int(cursor.rowcount)

    def _delete_or_count(self, *, deployment_id, principal_id, delete):
        from psycopg2 import sql

        operation = "DELETE FROM {table}" if delete else "SELECT COUNT(*) FROM {table}"
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(operation + " WHERE deployment_id=%s AND principal_id=%s").format(
                        table=sql.Identifier(self.table)
                    ),
                    (deployment_id, principal_id),
                )
                return int(cursor.rowcount if delete else cursor.fetchone()[0])

    @staticmethod
    def _from_row(row):
        return PrincipalMemoryExportRecord(
            schema_version=row[0],
            export_ref=row[1],
            deployment_id=row[2],
            principal_id=row[3],
            payload=row[4],
            created_at=row[5],
            expires_at=row[6],
        )

    def _ensure_schema(self):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            schema_version TEXT NOT NULL,
                            export_ref TEXT PRIMARY KEY,
                            deployment_id TEXT NOT NULL,
                            principal_id TEXT NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL,
                            CHECK (expires_at > created_at)
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )


class PostgresPrincipalMemoryDeletionTombstoneStore(
    _PostgresPrincipalMemoryStore
):
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
        clock=None,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        super().__init__(
            dsn=dsn,
            connection_provider=connection_provider,
            table_prefix=table_prefix,
            schema_mode=schema_mode,
        )
        self.table = f"{table_prefix}_principal_memory_tombs"
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def record_requested(self, *, deployment_id, principal_id):
        from psycopg2 import sql

        now = self.clock()
        digest = _tombstone_digest(
            deployment_id=deployment_id,
            principal_id=principal_id,
            requested_at=now,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                current = self._get_latest_with_cursor(
                    cursor,
                    deployment_id=deployment_id,
                    principal_id=principal_id,
                )
                if current is not None and current.status in {"requested", "failed"}:
                    return current
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            schema_version,tombstone_ref,deployment_id,
                            principal_id,requested_at,completed_at,replayed_at,
                            status,failed_stage,integrity_sha256
                        ) VALUES (
                            'principal-memory-deletion-tombstone-v1',%s,%s,%s,
                            %s,NULL,NULL,'requested',NULL,%s
                        )
                        ON CONFLICT (tombstone_ref) DO NOTHING
                        RETURNING schema_version,tombstone_ref,deployment_id,
                            principal_id,requested_at,completed_at,replayed_at,
                            status,failed_stage,integrity_sha256
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (f"pm-delete-{digest}", deployment_id, principal_id, now, digest),
                )
                row = cursor.fetchone()
        return self._from_row(row) if row else self.get_by_ref(f"pm-delete-{digest}")

    def mark(self, tombstone, *, status, failed_stage=None):
        from psycopg2 import sql

        self.validate(tombstone)
        if status not in {"failed", "completed", "replayed"}:
            raise ValueError("principal deletion tombstone status is invalid")
        now = self.clock()
        completed_at = now if status == "completed" else tombstone.completed_at
        replayed_at = now if status == "replayed" else tombstone.replayed_at
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {table} SET status=%s,failed_stage=%s,
                            completed_at=%s,replayed_at=%s
                        WHERE tombstone_ref=%s AND deployment_id=%s
                          AND principal_id=%s AND integrity_sha256=%s
                        RETURNING schema_version,tombstone_ref,deployment_id,
                            principal_id,requested_at,completed_at,replayed_at,
                            status,failed_stage,integrity_sha256
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        status,
                        failed_stage,
                        completed_at,
                        replayed_at,
                        tombstone.tombstone_ref,
                        tombstone.deployment_id,
                        tombstone.principal_id,
                        tombstone.integrity_sha256,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("principal deletion tombstone changed")
        return self._from_row(row)

    def import_tombstone(self, tombstone):
        self.validate(tombstone)
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            schema_version,tombstone_ref,deployment_id,
                            principal_id,requested_at,completed_at,replayed_at,
                            status,failed_stage,integrity_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (tombstone_ref) DO NOTHING
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        tombstone.schema_version,
                        tombstone.tombstone_ref,
                        tombstone.deployment_id,
                        tombstone.principal_id,
                        tombstone.requested_at,
                        tombstone.completed_at,
                        tombstone.replayed_at,
                        tombstone.status,
                        tombstone.failed_stage,
                        tombstone.integrity_sha256,
                    ),
                )
        current = self.get_by_ref(tombstone.tombstone_ref)
        if current is None or (
            current.tombstone_ref != tombstone.tombstone_ref
            or current.integrity_sha256 != tombstone.integrity_sha256
        ):
            raise RuntimeError("principal deletion tombstone conflict")
        return current

    @staticmethod
    def validate(tombstone):
        expected = _tombstone_digest(
            deployment_id=tombstone.deployment_id,
            principal_id=tombstone.principal_id,
            requested_at=tombstone.requested_at,
        )
        if expected != tombstone.integrity_sha256:
            raise ValueError("principal deletion tombstone integrity mismatch")

    def get(self, *, deployment_id, principal_id):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                return self._get_latest_with_cursor(
                    cursor,
                    deployment_id=deployment_id,
                    principal_id=principal_id,
                )

    def get_by_ref(self, tombstone_ref):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT schema_version,tombstone_ref,deployment_id,"
                        "principal_id,requested_at,completed_at,replayed_at,"
                        "status,failed_stage,integrity_sha256 FROM {table} "
                        "WHERE tombstone_ref=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (tombstone_ref,),
                )
                row = cursor.fetchone()
        return self._from_row(row) if row else None

    def _get_latest_with_cursor(self, cursor, *, deployment_id, principal_id):
        from psycopg2 import sql

        cursor.execute(
            sql.SQL(
                "SELECT schema_version,tombstone_ref,deployment_id,"
                "principal_id,requested_at,completed_at,replayed_at,"
                "status,failed_stage,integrity_sha256 FROM {table} "
                "WHERE deployment_id=%s AND principal_id=%s "
                "ORDER BY requested_at DESC,tombstone_ref DESC LIMIT 1"
            ).format(table=sql.Identifier(self.table)),
            (deployment_id, principal_id),
        )
        row = cursor.fetchone()
        return self._from_row(row) if row else None

    def is_write_blocked(self, *, deployment_id, principal_id) -> bool:
        current = self.get(
            deployment_id=deployment_id, principal_id=principal_id
        )
        return bool(current and current.status in {"requested", "failed"})

    @staticmethod
    def _lock_key(deployment_id, principal_id):
        return f"principal-memory-deletion:{deployment_id}:{principal_id}"

    @contextmanager
    def writer_guard(self, *, deployment_id, principal_id):
        observed = self.get(
            deployment_id=deployment_id, principal_id=principal_id
        )
        observed_state = (
            (observed.tombstone_ref, observed.status) if observed else None
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                    (self._lock_key(deployment_id, principal_id),),
                )
                current = self._get_latest_with_cursor(
                    cursor,
                    deployment_id=deployment_id,
                    principal_id=principal_id,
                )
                current_state = (
                    (current.tombstone_ref, current.status) if current else None
                )
                if current_state != observed_state or (
                    current and current.status in {"requested", "failed"}
                ):
                    raise PermissionError(
                        "principal memory deletion fence is active"
                    )
                yield

    @contextmanager
    def deletion_guard(self, *, deployment_id, principal_id):
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                    (self._lock_key(deployment_id, principal_id),),
                )
                yield

    @staticmethod
    def _from_row(row):
        return PrincipalMemoryDeletionTombstone(
            schema_version=row[0],
            tombstone_ref=row[1],
            deployment_id=row[2],
            principal_id=row[3],
            requested_at=row[4],
            completed_at=row[5],
            replayed_at=row[6],
            status=row[7],
            failed_stage=row[8],
            integrity_sha256=row[9],
        )

    def _ensure_schema(self):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            schema_version TEXT NOT NULL,
                            tombstone_ref TEXT NOT NULL,
                            deployment_id TEXT NOT NULL,
                            principal_id TEXT NOT NULL,
                            requested_at TIMESTAMPTZ NOT NULL,
                            completed_at TIMESTAMPTZ NULL,
                            replayed_at TIMESTAMPTZ NULL,
                            status TEXT NOT NULL CHECK (
                                status IN ('requested','failed','completed','replayed')
                            ),
                            failed_stage TEXT NULL,
                            integrity_sha256 TEXT NOT NULL CHECK (
                                integrity_sha256 ~ '^[0-9a-f]{{64}}$'
                            ),
                            PRIMARY KEY (tombstone_ref)
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )
                cursor.execute(
                    "SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass "
                    "AND contype='p' AND pg_get_constraintdef(oid)<>%s",
                    (self.table, "PRIMARY KEY (tombstone_ref)"),
                )
                old_primary = cursor.fetchone()
                if old_primary is not None:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {table} DROP CONSTRAINT {constraint}").format(
                            table=sql.Identifier(self.table),
                            constraint=sql.Identifier(old_primary[0]),
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {table} ADD PRIMARY KEY (tombstone_ref)"
                        ).format(table=sql.Identifier(self.table))
                    )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} "
                        "(deployment_id,principal_id,requested_at DESC)"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table.split("_principal_memory_tombs")[0],
                                "principal_memory_tombs_principal_requested_idx",
                            )
                        ),
                        table=sql.Identifier(self.table),
                    )
                )


class PostgresPrincipalMemorySafeRefStore(_PostgresPrincipalMemoryStore):
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
        clock=None,
        ref_factory=None,
        ttl_seconds=900,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ref_factory = ref_factory or (lambda: f"pm-ref-{uuid4().hex}")
        self.ttl_seconds = ttl_seconds
        super().__init__(
            dsn=dsn,
            connection_provider=connection_provider,
            table_prefix=table_prefix,
            schema_mode=schema_mode,
        )
        self.table = f"{table_prefix}_principal_memory_refs"
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def issue(self, fact):
        from psycopg2 import sql

        now = self.clock()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT safe_ref FROM {table} WHERE deployment_id=%s "
                        "AND principal_id=%s AND fact_id=%s AND fact_version=%s "
                        "AND expires_at>%s ORDER BY expires_at DESC LIMIT 1"
                    ).format(table=sql.Identifier(self.table)),
                    (
                        fact.deployment_id,
                        fact.principal_id,
                        fact.fact_id,
                        fact.version,
                        now,
                    ),
                )
                existing = cursor.fetchone()
        if existing is not None:
            return existing[0]
        record = PrincipalMemorySafeRefRecord(
            safe_ref=self.ref_factory(),
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            fact_version=fact.version,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (safe_ref,deployment_id,principal_id,"
                        "fact_id,fact_version,expires_at) VALUES (%s,%s,%s,%s,%s,%s)"
                    ).format(table=sql.Identifier(self.table)),
                    (
                        record.safe_ref,
                        record.deployment_id,
                        record.principal_id,
                        record.fact_id,
                        record.fact_version,
                        record.expires_at,
                    ),
                )
        return record.safe_ref

    def resolve(self, safe_ref, *, deployment_id, principal_id, fact_store):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT safe_ref,deployment_id,principal_id,fact_id,"
                        "fact_version,expires_at FROM {table} WHERE safe_ref=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (safe_ref,),
                )
                row = cursor.fetchone()
        record = PrincipalMemorySafeRefRecord(*row) if row else None
        if (
            record is None
            or record.expires_at <= self.clock()
            or record.deployment_id != deployment_id
            or record.principal_id != principal_id
        ):
            raise PrincipalMemorySafeRefInvalid(
                "principal memory safe reference is stale"
            )
        fact = fact_store.get(
            deployment_id=deployment_id,
            principal_id=principal_id,
            fact_id=record.fact_id,
        )
        if fact is None or fact.version != record.fact_version:
            raise PrincipalMemorySafeRefInvalid(
                "principal memory safe reference is stale"
            )
        return fact

    def purge(self, *, deployment_id, principal_id):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {table} WHERE deployment_id=%s AND principal_id=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (deployment_id, principal_id),
                )
                return int(cursor.rowcount)

    def cleanup_expired(self, *, now=None, batch_size=200):
        now = now or self.clock()
        if now.tzinfo is None:
            raise ValueError("principal memory cleanup time must be timezone-aware")
        if batch_size < 1:
            raise ValueError("principal memory cleanup batch size must be positive")
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {table} WHERE ctid IN ("
                        "SELECT ctid FROM {table} WHERE expires_at<=%s "
                        "ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED)"
                    ).format(table=sql.Identifier(self.table)),
                    (now, batch_size),
                )
                return int(cursor.rowcount)

    def _ensure_schema(self):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            safe_ref TEXT PRIMARY KEY,
                            deployment_id TEXT NOT NULL,
                            principal_id TEXT NOT NULL,
                            fact_id TEXT NOT NULL CHECK (fact_id ~ '^[0-9a-f]{{64}}$'),
                            fact_version INTEGER NOT NULL CHECK (fact_version > 0),
                            expires_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )
