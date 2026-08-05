from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.principal_memory_ledger import GENESIS_HEAD_SHA256


WATERMARK_SCHEMA_VERSION = "principal-memory-ledger-watermark-v1"
WATERMARK_KEY = "operator-ledger"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PrincipalMemoryLedgerWatermark:
    last_applied_ledger_event_count: int
    last_applied_ledger_head_sha256: str
    last_applied_at: datetime | None


class PostgresPrincipalMemoryLedgerWatermarkStore:
    def __init__(
        self,
        *,
        dsn=None,
        connection_provider=None,
        table_prefix="interview",
        schema_mode=None,
    ):
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            owned = True
        else:
            owned = False
        self._connection_provider = connection_provider
        self.table = f"{table_prefix}_principal_memory_ledger_watermark"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def get(self) -> PrincipalMemoryLedgerWatermark:
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT schema_version,last_applied_ledger_event_count,"
                        "last_applied_ledger_head_sha256,last_applied_at "
                        "FROM {table} WHERE singleton_key=%s"
                    ).format(table=sql.Identifier(self.table)),
                    (WATERMARK_KEY,),
                )
                row = cursor.fetchone()
        if row is None or row[0] != WATERMARK_SCHEMA_VERSION:
            raise RuntimeError("principal memory ledger watermark is missing")
        return PrincipalMemoryLedgerWatermark(int(row[1]), str(row[2]), row[3])

    def advance(
        self,
        *,
        expected_event_count,
        expected_head_sha256,
        new_event_count,
        new_head_sha256,
        applied_at,
    ):
        if expected_event_count < 0 or new_event_count < 0:
            raise ValueError("ledger watermark event count must be non-negative")
        if (
            _SHA256.fullmatch(str(expected_head_sha256)) is None
            or _SHA256.fullmatch(str(new_head_sha256)) is None
        ):
            raise ValueError("ledger watermark head must be a SHA-256 digest")
        if new_event_count != expected_event_count + 1:
            raise ValueError("ledger watermark must advance exactly one event")
        if new_head_sha256 == expected_head_sha256:
            raise ValueError("ledger watermark event must change the head")
        if applied_at.tzinfo is None or applied_at.utcoffset() is None:
            raise ValueError("ledger watermark time must be timezone-aware")
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET "
                        "last_applied_ledger_event_count=%s,"
                        "last_applied_ledger_head_sha256=%s,last_applied_at=%s "
                        "WHERE singleton_key=%s AND schema_version=%s "
                        "AND last_applied_ledger_event_count=%s "
                        "AND last_applied_ledger_head_sha256=%s "
                        "RETURNING last_applied_ledger_event_count,"
                        "last_applied_ledger_head_sha256,last_applied_at"
                    ).format(table=sql.Identifier(self.table)),
                    (
                        new_event_count,
                        new_head_sha256,
                        applied_at,
                        WATERMARK_KEY,
                        WATERMARK_SCHEMA_VERSION,
                        expected_event_count,
                        expected_head_sha256,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("principal memory ledger watermark conflict")
        return PrincipalMemoryLedgerWatermark(int(row[0]), str(row[1]), row[2])

    def _ensure_schema(self):
        from psycopg2 import sql
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            singleton_key TEXT PRIMARY KEY CHECK (singleton_key='operator-ledger'),
                            schema_version TEXT NOT NULL CHECK (schema_version='principal-memory-ledger-watermark-v1'),
                            last_applied_ledger_event_count BIGINT NOT NULL CHECK (last_applied_ledger_event_count>=0),
                            last_applied_ledger_head_sha256 TEXT NOT NULL CHECK (last_applied_ledger_head_sha256 ~ '^[0-9a-f]{{64}}$'),
                            last_applied_at TIMESTAMPTZ NULL,
                            CHECK (
                                (last_applied_ledger_event_count=0 AND
                                 last_applied_ledger_head_sha256={genesis}) OR
                                last_applied_ledger_event_count>0
                            )
                        );
                        INSERT INTO {table} (
                            singleton_key,schema_version,
                            last_applied_ledger_event_count,
                            last_applied_ledger_head_sha256,last_applied_at
                        ) VALUES (%s,%s,0,%s,NULL)
                        ON CONFLICT (singleton_key) DO NOTHING
                        """
                    ).format(
                        table=sql.Identifier(self.table),
                        genesis=sql.Literal(GENESIS_HEAD_SHA256),
                    ),
                    (WATERMARK_KEY, WATERMARK_SCHEMA_VERSION, GENESIS_HEAD_SHA256),
                )
