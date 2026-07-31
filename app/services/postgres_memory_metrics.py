from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.services.memory_metrics import MemoryMetricEvent
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations


VALUE_FIELDS = (
    "event_count",
    "source_count",
    "selected_count",
    "dropped_count",
    "truncated_count",
    "estimated_input_tokens",
    "provider_input_tokens",
    "provider_output_tokens",
    "latency_ms",
    "attempts",
    "size_bytes",
    "queue_age_ms",
    "active_count",
    "superseded_count",
    "referenced_count",
    "orphan_count",
)


def canonical_dimensions(dimensions: dict) -> tuple[str, str]:
    payload = json.dumps(
        dimensions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PostgresMemoryMetricStore:
    store_kind = "postgres_aggregate"

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
        clock=None,
        minimum_language_samples: int = 5,
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
        self.table = f"{table_prefix}_memory_metric_buckets"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.minimum_language_samples = minimum_language_samples
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=self._provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def publish(self, event: MemoryMetricEvent | dict) -> None:
        validated = MemoryMetricEvent.model_validate(event)
        if validated.observed_at.tzinfo is None:
            raise ValueError("memory metric timestamp must be timezone-aware")
        dimensions = validated.dimensions.model_dump(exclude_none=True)
        dimensions_json, dimensions_sha256 = canonical_dimensions(dimensions)
        values = validated.values.model_dump()
        from psycopg2 import sql

        columns = sql.SQL(",").join(sql.Identifier(name) for name in VALUE_FIELDS)
        value_placeholders = sql.SQL(",").join(sql.Placeholder() for _ in VALUE_FIELDS)
        increments = sql.SQL(",").join(
            sql.SQL("{field}={table}.{field}+EXCLUDED.{field}").format(
                field=sql.Identifier(name),
                table=sql.Identifier(self.table),
            )
            for name in VALUE_FIELDS
        )
        params = (
            validated.observed_at,
            validated.metric_code,
            dimensions_sha256,
            dimensions_json,
            *(values[name] for name in VALUE_FIELDS),
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            bucket_start,bucket_width,metric_code,
                            dimensions_sha256,dimensions,{columns}
                        ) VALUES (
                            date_trunc('minute', %s::timestamptz),'minute',
                            %s,%s,%s::jsonb,{placeholders}
                        )
                        ON CONFLICT (
                            bucket_start,bucket_width,metric_code,dimensions_sha256
                        ) DO UPDATE SET {increments},updated_at=NOW()
                        """
                    ).format(
                        table=sql.Identifier(self.table),
                        columns=columns,
                        placeholders=value_placeholders,
                        increments=increments,
                    ),
                    params,
                )

    def aggregate(self, *, window_minutes: int) -> dict:
        if window_minutes not in {15, 60, 360, 1440}:
            raise ValueError("unsupported memory metrics window")
        observed_since = self.clock() - timedelta(minutes=window_minutes)
        from psycopg2 import sql

        sums = sql.SQL(",").join(
            sql.SQL("SUM({field}) AS {field}").format(field=sql.Identifier(name))
            for name in VALUE_FIELDS
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT metric_code,dimensions,{sums},MAX(bucket_start)
                        FROM {table}
                        WHERE bucket_width='minute' AND bucket_start >= %s
                        GROUP BY metric_code,dimensions_sha256,dimensions
                        ORDER BY metric_code,dimensions_sha256
                        """
                    ).format(table=sql.Identifier(self.table), sums=sums),
                    (observed_since,),
                )
                rows = cursor.fetchall()
        items = []
        latest = None
        for row in rows:
            metric_code, dimensions = row[0], dict(row[1])
            values = {name: int(row[index + 2]) for index, name in enumerate(VALUE_FIELDS)}
            row_latest = row[2 + len(VALUE_FIELDS)]
            latest = max(latest, row_latest) if latest is not None else row_latest
            sample_status = "sufficient"
            if (
                metric_code == "provider_usage"
                and values["event_count"] < self.minimum_language_samples
            ):
                sample_status = "insufficient_sample"
            items.append(
                {
                    "metric_code": metric_code,
                    "dimensions": dimensions,
                    "values": values,
                    "sample_status": sample_status,
                }
            )
        return {
            "schema_version": "memory-metrics-v1",
            "window_minutes": window_minutes,
            "observed_since": observed_since.isoformat(),
            "store_kind": self.store_kind,
            "data_complete": True,
            "latest_bucket_at": latest.isoformat() if latest else None,
            "items": items,
        }

    def diagnostics(self) -> dict:
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT MAX(bucket_start) FROM {table}").format(
                        table=sql.Identifier(self.table)
                    )
                )
                row = cursor.fetchone()
        latest = row[0] if row else None
        return {
            "store_kind": self.store_kind,
            "data_complete": True,
            "latest_bucket_at": latest.isoformat() if latest else None,
        }

    def rollup(self, *, batch_size: int = 1000) -> int:
        if batch_size < 1:
            raise ValueError("memory metric batch size must be positive")
        from psycopg2 import sql

        sums = sql.SQL(",").join(
            sql.SQL("SUM({field}) AS {field}").format(field=sql.Identifier(name))
            for name in VALUE_FIELDS
        )
        columns = sql.SQL(",").join(sql.Identifier(name) for name in VALUE_FIELDS)
        replacements = sql.SQL(",").join(
            sql.SQL("{field}=EXCLUDED.{field}").format(field=sql.Identifier(name))
            for name in VALUE_FIELDS
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH source AS (
                            SELECT date_trunc('hour',bucket_start) AS hour_start,
                                   metric_code,dimensions_sha256,dimensions,{sums}
                            FROM {table}
                            WHERE bucket_width='minute'
                              AND bucket_start < date_trunc('hour',NOW())
                            GROUP BY hour_start,metric_code,dimensions_sha256,dimensions
                            ORDER BY hour_start
                            LIMIT %s
                        )
                        INSERT INTO {table} (
                            bucket_start,bucket_width,metric_code,
                            dimensions_sha256,dimensions,{columns}
                        )
                        SELECT hour_start,'hour',metric_code,dimensions_sha256,
                               dimensions,{columns}
                        FROM source
                        ON CONFLICT (
                            bucket_start,bucket_width,metric_code,dimensions_sha256
                        ) DO UPDATE SET {replacements},updated_at=NOW()
                        """
                    ).format(
                        table=sql.Identifier(self.table),
                        sums=sums,
                        columns=columns,
                        replacements=replacements,
                    ),
                    (batch_size,),
                )
                return int(cursor.rowcount)

    def cleanup(
        self,
        *,
        minute_retention_days: int = 30,
        hour_retention_days: int = 180,
        batch_size: int = 1000,
    ) -> dict[str, int]:
        if min(minute_retention_days, hour_retention_days, batch_size) < 1:
            raise ValueError("memory metric retention values must be positive")
        from psycopg2 import sql

        deleted = {}
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                for width, days in (
                    ("minute", minute_retention_days),
                    ("hour", hour_retention_days),
                ):
                    cursor.execute(
                        sql.SQL(
                            """
                            DELETE FROM {table} WHERE ctid IN (
                                SELECT ctid FROM {table}
                                WHERE bucket_width=%s
                                  AND bucket_start < NOW()-(%s*INTERVAL '1 day')
                                ORDER BY bucket_start LIMIT %s
                            )
                            """
                        ).format(table=sql.Identifier(self.table)),
                        (width, days, batch_size),
                    )
                    deleted[f"{width}_deleted"] = int(cursor.rowcount)
        return deleted

    def _ensure_schema(self) -> None:
        from psycopg2 import sql

        numeric_columns = sql.SQL(",").join(
            sql.SQL("{field} BIGINT NOT NULL DEFAULT 0 CHECK ({field} >= 0)").format(
                field=sql.Identifier(name)
            )
            for name in VALUE_FIELDS
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            bucket_start TIMESTAMPTZ NOT NULL,
                            bucket_width TEXT NOT NULL
                                CHECK (bucket_width IN ('minute','hour')),
                            metric_code TEXT NOT NULL,
                            dimensions_sha256 TEXT NOT NULL
                                CHECK (dimensions_sha256 ~ '^[0-9a-f]{{64}}$'),
                            dimensions JSONB NOT NULL,
                            {numeric_columns},
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (
                                bucket_start,bucket_width,metric_code,
                                dimensions_sha256
                            )
                        )
                        """
                    ).format(
                        table=sql.Identifier(self.table),
                        numeric_columns=numeric_columns,
                    )
                )
