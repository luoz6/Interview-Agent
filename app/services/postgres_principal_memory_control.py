from __future__ import annotations

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.principal_memory_control import (
    PrincipalMemoryControl,
    PrincipalMemoryControlConflict,
)


class PostgresPrincipalMemoryControlStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ):
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self._connection_provider = connection_provider
        self.table = f"{table_prefix}_principal_memory_controls"
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=self._provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def get_global(self, *, deployment_id: str, principal_id: str):
        return self._get(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_key="",
        )

    def set_global(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        enabled: bool,
        updated_at,
        expected_version: int | None = None,
    ):
        return self._set(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_key="",
            enabled=enabled,
            updated_at=updated_at,
            expected_version=expected_version,
        )

    def get_session(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        session_id: str,
    ):
        return self._get(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_key=session_id,
        )

    def set_session(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        session_id: str,
        enabled: bool,
        updated_at,
        expected_version: int | None = None,
    ):
        return self._set(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_key=session_id,
            enabled=enabled,
            updated_at=updated_at,
            expected_version=expected_version,
        )

    def purge(self, *, deployment_id: str, principal_id: str) -> int:
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

    def count(self, *, deployment_id: str, principal_id: str) -> int:
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FROM {table} WHERE deployment_id=%s "
                        "AND principal_id=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (deployment_id, principal_id),
                )
                return int(cursor.fetchone()[0])

    def _get(self, *, deployment_id: str, principal_id: str, session_key: str):
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT schema_version,deployment_id,principal_id,"
                        "session_key,enabled,updated_at,version FROM {table} "
                        "WHERE deployment_id=%s AND principal_id=%s AND session_key=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (deployment_id, principal_id, session_key),
                )
                row = cursor.fetchone()
        return self._from_row(row) if row else None

    def _set(
        self,
        *,
        deployment_id,
        principal_id,
        session_key,
        enabled,
        updated_at,
        expected_version,
    ):
        from psycopg2 import sql

        if expected_version is not None:
            current = self._get(
                deployment_id=deployment_id,
                principal_id=principal_id,
                session_key=session_key,
            )
            current_version = current.version if current is not None else 0
            if current_version != expected_version:
                raise PrincipalMemoryControlConflict(
                    "principal memory control version changed"
                )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            schema_version,deployment_id,principal_id,session_key,
                            enabled,updated_at,version
                        ) VALUES ('principal-memory-control-v1',%s,%s,%s,%s,%s,1)
                        ON CONFLICT (deployment_id,principal_id,session_key)
                        DO UPDATE SET enabled=EXCLUDED.enabled,
                            updated_at=EXCLUDED.updated_at,
                            version={table}.version+1
                        WHERE (%s IS NULL OR {table}.version=%s)
                          AND {table}.enabled IS DISTINCT FROM EXCLUDED.enabled
                        RETURNING schema_version,deployment_id,principal_id,
                            session_key,enabled,updated_at,version
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        deployment_id,
                        principal_id,
                        session_key,
                        enabled,
                        updated_at,
                        expected_version,
                        expected_version,
                    ),
                )
                row = cursor.fetchone()
        if row is not None:
            return self._from_row(row)
        current = self._get(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_key=session_key,
        )
        if current is not None and current.enabled == enabled and (
            expected_version is None or current.version == expected_version
        ):
            return current
        raise PrincipalMemoryControlConflict(
            "principal memory control version changed"
        )

    @staticmethod
    def _from_row(row):
        session_key = row[3]
        return PrincipalMemoryControl(
            schema_version=row[0],
            deployment_id=row[1],
            principal_id=row[2],
            scope="session" if session_key else "global",
            session_id=session_key or None,
            enabled=row[4],
            updated_at=row[5],
            version=row[6],
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
                            deployment_id TEXT NOT NULL,
                            principal_id TEXT NOT NULL,
                            session_key TEXT NOT NULL,
                            enabled BOOLEAN NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            version INTEGER NOT NULL CHECK (version > 0),
                            PRIMARY KEY (deployment_id,principal_id,session_key)
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )
