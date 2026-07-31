from __future__ import annotations

from app.services.in_memory_principal_memory import transition_fact
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.principal_memory_contracts import PrincipalMemoryFact


class PostgresPrincipalMemoryFactStore:
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
        self.table = f"{table_prefix}_principal_memory_facts"
        self.effects_table = f"{table_prefix}_principal_memory_effects"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider, (self.table, self.effects_table)
            )

    def create_proposal(self, fact: PrincipalMemoryFact):
        if fact.status != "proposed" or fact.user_confirmed:
            raise ValueError("new principal memory facts must be unconfirmed proposals")
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} ({columns}) VALUES ({values}) "
                        "ON CONFLICT (deployment_id,principal_id,fact_id) DO NOTHING"
                    ).format(
                        table=sql.Identifier(self.table),
                        columns=sql.SQL(self._columns()),
                        values=sql.SQL(",").join(sql.Placeholder() for _ in range(24)),
                    ),
                    self._params(fact),
                )
        return self.get(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
        )

    def get(self, *, deployment_id: str, principal_id: str, fact_id: str):
        return self._fetch_one(
            "deployment_id=%s AND principal_id=%s AND fact_id=%s",
            (deployment_id, principal_id, fact_id),
        )

    def transition(self, **kwargs):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT {columns} FROM {table} WHERE deployment_id=%s "
                            "AND principal_id=%s AND fact_id=%s FOR UPDATE").format(
                        columns=sql.SQL(self._columns()), table=sql.Identifier(self.table)
                    ),
                    (kwargs["deployment_id"], kwargs["principal_id"], kwargs["fact_id"]),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                updated = transition_fact(
                    self._from_row(row),
                    expected_version=kwargs["expected_version"],
                    target_status=kwargs["target_status"],
                    now=kwargs["now"],
                    expires_at=kwargs.get("expires_at"),
                    supersedes_fact_id=kwargs.get("supersedes_fact_id"),
                )
                cursor.execute(
                    sql.SQL("UPDATE {table} SET status=%s,user_confirmed=%s,version=%s,"
                            "confirmed_at=%s,expires_at=%s,supersedes_fact_id=%s,"
                            "revoked_at=%s,deleted_at=%s WHERE deployment_id=%s AND "
                            "principal_id=%s AND fact_id=%s AND version=%s").format(
                        table=sql.Identifier(self.table)
                    ),
                    (
                        updated.status, updated.user_confirmed, updated.version,
                        updated.confirmed_at, updated.expires_at, updated.supersedes_fact_id,
                        updated.revoked_at, updated.deleted_at, updated.deployment_id,
                        updated.principal_id, updated.fact_id, kwargs["expected_version"],
                    ),
                )
                if cursor.rowcount != 1:
                    from app.services.in_memory_principal_memory import PrincipalMemoryConflict
                    raise PrincipalMemoryConflict("principal memory fact version conflict")
        return updated

    def list_by_principal(self, *, deployment_id, principal_id, limit, include_terminal=False):
        if limit < 1:
            raise ValueError("principal memory list limit must be positive")
        terminal = "" if include_terminal else " AND status IN ('proposed','active')"
        return self._fetch_many(
            "deployment_id=%s AND principal_id=%s" + terminal,
            (deployment_id, principal_id, limit),
            limit=limit,
        )

    def list_shadow_eligible(self, *, deployment_id, principal_id, now, limit):
        return self._fetch_many(
            "deployment_id=%s AND principal_id=%s AND status='active' "
            "AND user_confirmed=TRUE AND (expires_at IS NULL OR expires_at>%s)",
            (deployment_id, principal_id, now, limit),
            limit=limit,
        )

    def expire_batch(self, *, now, limit):
        if limit < 1:
            raise ValueError("principal memory expire limit must be positive")
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {table} SET status='expired',version=version+1 "
                            "WHERE ctid IN (SELECT ctid FROM {table} WHERE status='active' "
                            "AND expires_at<=%s ORDER BY expires_at LIMIT %s)").format(
                        table=sql.Identifier(self.table)
                    ), (now, limit)
                )
                return int(cursor.rowcount)

    def purge_by_session(self, source_session_id):
        return self._delete("source_session_id=%s", (source_session_id,))

    def purge_by_principal(self, *, deployment_id, principal_id):
        return self._delete(
            "deployment_id=%s AND principal_id=%s", (deployment_id, principal_id)
        )

    def _delete(self, where, params):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(f"DELETE FROM {{table}} WHERE {where}").format(
                        table=sql.Identifier(self.table)
                    ), params
                )
                return int(cursor.rowcount)

    def _fetch_one(self, where, params):
        rows = self._fetch_many(where, (*params, 1), limit=1)
        return rows[0] if rows else None

    def _fetch_many(self, where, params, *, limit):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(f"SELECT {{columns}} FROM {{table}} WHERE {where} "
                            "ORDER BY created_at DESC,fact_id DESC LIMIT %s").format(
                        columns=sql.SQL(self._columns()), table=sql.Identifier(self.table)
                    ), params
                )
                return [self._from_row(row) for row in cursor.fetchall()]

    @staticmethod
    def _columns():
        return ("schema_version,fact_id,deployment_id,principal_id,fact_type,"
                "normalized_fact,confidence,authority,canonicalization_version,status,"
                "source_session_id,source_question_id,source_manifest_sha256,"
                "source_excerpt_sha256,consent_policy_version,taxonomy_version,"
                "user_confirmed,version,created_at,confirmed_at,expires_at,"
                "supersedes_fact_id,revoked_at,deleted_at")

    @staticmethod
    def _params(fact):
        return tuple(getattr(fact, name) for name in PostgresPrincipalMemoryFactStore._columns().split(","))

    @staticmethod
    def _from_row(row):
        return PrincipalMemoryFact(**dict(zip(PostgresPrincipalMemoryFactStore._columns().split(","), row)))

    def _ensure_schema(self):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {table} (
                        schema_version TEXT NOT NULL,fact_id TEXT NOT NULL,
                        deployment_id TEXT NOT NULL,principal_id TEXT NOT NULL,
                        fact_type TEXT NOT NULL,normalized_fact TEXT NOT NULL CHECK (length(normalized_fact)<=512),
                        confidence DOUBLE PRECISION NOT NULL CHECK (confidence>=0 AND confidence<=1),
                        authority TEXT NOT NULL,canonicalization_version TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('proposed','active','rejected','superseded','expired','revoked','deleted')),
                        source_session_id TEXT NOT NULL,source_question_id TEXT,
                        source_manifest_sha256 TEXT NOT NULL CHECK (source_manifest_sha256 ~ '^[0-9a-f]{{64}}$'),
                        source_excerpt_sha256 TEXT NOT NULL CHECK (source_excerpt_sha256 ~ '^[0-9a-f]{{64}}$'),
                        consent_policy_version TEXT NOT NULL,taxonomy_version TEXT NOT NULL,
                        user_confirmed BOOLEAN NOT NULL DEFAULT FALSE,version INTEGER NOT NULL CHECK (version>0),
                        created_at TIMESTAMPTZ NOT NULL,confirmed_at TIMESTAMPTZ,expires_at TIMESTAMPTZ,
                        supersedes_fact_id TEXT,revoked_at TIMESTAMPTZ,deleted_at TIMESTAMPTZ,
                        PRIMARY KEY (deployment_id,principal_id,fact_id),
                        CHECK (status<>'active' OR (user_confirmed=TRUE AND confirmed_at IS NOT NULL)),
                        CHECK (revoked_at IS NULL OR status='revoked'),
                        CHECK (deleted_at IS NULL OR status='deleted')
                    );
                    CREATE INDEX IF NOT EXISTS {principal_idx} ON {table} (deployment_id,principal_id,status,created_at DESC);
                    CREATE INDEX IF NOT EXISTS {session_idx} ON {table} (source_session_id);
                    CREATE TABLE IF NOT EXISTS {effects} (
                        effect_id TEXT PRIMARY KEY,deployment_id TEXT NOT NULL,principal_id TEXT NOT NULL,
                        source_session_id TEXT NOT NULL,status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL
                    )
                """).format(
                    table=sql.Identifier(self.table), effects=sql.Identifier(self.effects_table),
                    principal_idx=sql.Identifier(
                        runtime_schema_identifier(
                            self.table.split("_principal_memory_facts")[0],
                            "principal_memory_facts_principal_idx",
                        )
                    ),
                    session_idx=sql.Identifier(
                        runtime_schema_identifier(
                            self.table.split("_principal_memory_facts")[0],
                            "principal_memory_facts_session_idx",
                        )
                    ),
                ))
