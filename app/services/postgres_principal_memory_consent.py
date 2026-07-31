from __future__ import annotations

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.principal_memory_consent import PrincipalMemoryConsent


class PostgresPrincipalMemoryConsentStore:
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
        self.table = f"{table_prefix}_principal_memory_consents"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def get_current(self, *, deployment_id: str, principal_id: str):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT schema_version,deployment_id,principal_id,policy_version,"
                        "allowed_purposes,granted_at,revoked_at,version FROM {table} "
                        "WHERE deployment_id=%s AND principal_id=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (deployment_id, principal_id),
                )
                row = cursor.fetchone()
        return self._from_row(row) if row else None

    def grant(self, consent: PrincipalMemoryConsent):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            schema_version,deployment_id,principal_id,policy_version,
                            allowed_purposes,granted_at,revoked_at,version
                        ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,NULL,1)
                        ON CONFLICT (deployment_id,principal_id) DO UPDATE SET
                            schema_version=EXCLUDED.schema_version,
                            policy_version=EXCLUDED.policy_version,
                            allowed_purposes=EXCLUDED.allowed_purposes,
                            granted_at=EXCLUDED.granted_at,
                            revoked_at=NULL,
                            version={table}.version+1
                        RETURNING schema_version,deployment_id,principal_id,
                            policy_version,allowed_purposes,granted_at,revoked_at,version
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        consent.schema_version,
                        consent.deployment_id,
                        consent.principal_id,
                        consent.policy_version,
                        __import__("json").dumps(consent.allowed_purposes),
                        consent.granted_at,
                    ),
                )
                row = cursor.fetchone()
        return self._from_row(row)

    def revoke(self, *, deployment_id: str, principal_id: str, revoked_at):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET revoked_at=COALESCE(revoked_at,%s),"
                        "version=CASE WHEN revoked_at IS NULL THEN version+1 ELSE version END "
                        "WHERE deployment_id=%s AND principal_id=%s RETURNING "
                        "schema_version,deployment_id,principal_id,policy_version,"
                        "allowed_purposes,granted_at,revoked_at,version"
                    ).format(table=sql.Identifier(self.table)),
                    (revoked_at, deployment_id, principal_id),
                )
                row = cursor.fetchone()
        return self._from_row(row) if row else None

    def purge(self, *, deployment_id: str, principal_id: str) -> int:
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {table} WHERE deployment_id=%s AND principal_id=%s").format(
                        table=sql.Identifier(self.table)
                    ),
                    (deployment_id, principal_id),
                )
                return int(cursor.rowcount)

    @staticmethod
    def _from_row(row):
        return PrincipalMemoryConsent(
            schema_version=row[0], deployment_id=row[1], principal_id=row[2],
            policy_version=row[3], allowed_purposes=list(row[4]), granted_at=row[5],
            revoked_at=row[6], version=row[7],
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
                            policy_version TEXT NOT NULL,
                            allowed_purposes JSONB NOT NULL,
                            granted_at TIMESTAMPTZ NOT NULL,
                            revoked_at TIMESTAMPTZ,
                            version INTEGER NOT NULL CHECK (version > 0),
                            PRIMARY KEY (deployment_id,principal_id)
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )
