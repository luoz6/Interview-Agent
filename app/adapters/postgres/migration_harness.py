from __future__ import annotations

from collections.abc import Callable

from app.ports.postgres_migrations import (
    MigrationExecution,
    MigrationHarnessResult,
    MigrationIdempotencyFailure,
    PostgresMigrationAdapterPort,
)
from app.ports.postgres_scope import OwnedPostgresLease, OwnedPostgresScopePort
from app.runtime.config.compatibility import derive_pgvector_table_names
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_identifiers import runtime_table_names
from app.services.postgres_runtime_migrations import migrate_postgres_runtime
from app.services.postgres_schema import validate_relations
from app.services.postgres_schema_contract import RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX


class RuntimeMigrationHarness:
    def __init__(
        self,
        *,
        scope: OwnedPostgresScopePort,
        adapter: PostgresMigrationAdapterPort,
    ) -> None:
        self._scope = scope
        self._adapter = adapter

    def apply_and_validate(
        self,
        lease: OwnedPostgresLease,
    ) -> MigrationHarnessResult:
        self._scope.assert_owned(lease)
        first = self._adapter.apply(lease)
        self._scope.assert_owned(lease)
        self._adapter.validate(lease)
        self._scope.assert_owned(lease)
        second = self._adapter.apply(lease)
        self._scope.assert_owned(lease)
        self._adapter.validate(lease)
        if second.applied:
            raise MigrationIdempotencyFailure(
                "the second migration apply changed the owned schema"
            )
        if (
            first.migration_id != second.migration_id
            or first.checksum != second.checksum
        ):
            raise MigrationIdempotencyFailure(
                "migration identity changed between idempotency passes"
            )
        return MigrationHarnessResult(
            migration_id=first.migration_id,
            checksum=first.checksum,
            first_apply_changed_schema=first.applied,
            second_apply_changed_schema=second.applied,
            validation_passed=True,
        )

    def validate_only(self, lease: OwnedPostgresLease) -> None:
        self._scope.assert_owned(lease)
        self._adapter.validate(lease)
        self._scope.assert_owned(lease)


class PostgresRuntimeMigrationAdapter:
    def __init__(
        self,
        *,
        dsn: str,
        embedding_provider,
        connect=None,
        vector_table_factory: Callable[[str], str] | None = None,
    ) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL migration adapter requires a DSN")
        self._dsn = dsn
        self._embedding_provider = embedding_provider
        self._connect = connect
        self._vector_table_factory = vector_table_factory or (
            lambda prefix: f"{prefix}_knowledge"
        )

    def _vector_table(self, lease: OwnedPostgresLease) -> str:
        table = self._vector_table_factory(lease.scope_prefix)
        if not table.startswith(f"{lease.scope_prefix}_"):
            raise ValueError("pgvector table must remain inside the owned scope")
        return table

    def apply(self, lease: OwnedPostgresLease) -> MigrationExecution:
        kwargs = {
            "dsn": self._dsn,
            "table_prefix": lease.scope_prefix,
            "pgvector_table": self._vector_table(lease),
            "embedding_provider": self._embedding_provider,
            "run_checkpointer_setup": False,
        }
        if self._connect is not None:
            kwargs["connect"] = self._connect
        result = migrate_postgres_runtime(**kwargs)
        return MigrationExecution(
            migration_id=result.migration_id,
            checksum=result.checksum,
            applied=result.applied,
        )

    def validate(self, lease: OwnedPostgresLease) -> None:
        provider = DirectPsycopg2ConnectionProvider(
            self._dsn,
            connect=self._connect,
            connect_kwargs={"connect_timeout": 3},
        )
        vector_table = self._vector_table(lease)
        versions_table, releases_table = derive_pgvector_table_names(vector_table)
        relation_names = set(runtime_table_names(lease.scope_prefix))
        relation_names.update(
            f"{lease.scope_prefix}{suffix}"
            for suffix in RUNTIME_REQUIRED_COLUMNS_BY_SUFFIX
            if suffix not in {"_versions", "_releases"}
        )
        relation_names.update({vector_table, versions_table, releases_table})
        validate_relations(
            provider,
            tuple(sorted(relation_names)),
        )
