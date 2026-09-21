"""PostgreSQL adapter for immutable orchestration path ownership."""

from __future__ import annotations

from datetime import datetime, timezone

from app.adapters.postgres.connections import ConnectionProvider
from app.adapters.postgres.runtime_repository_support import postgres_sql
from app.domain.interview.orchestration_cutover import (
    ExecutionPathBinding,
    ExecutionPathConflict,
    OrchestrationPath,
)


class PostgresExecutionPathBindingStore:
    """Use a primary key to serialize competing OLD/NEW claims."""

    _COLUMNS = "execution_id, orchestration_path, bound_at, schema_version"

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        table_name: str = "interview_execution_path_bindings",
    ) -> None:
        self._provider = connection_provider
        self.table_name = table_name

    def get(self, execution_id: str) -> ExecutionPathBinding | None:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"SELECT {self._COLUMNS} FROM {{table}} "
                        "WHERE execution_id=%s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (execution_id,),
                )
                row = cursor.fetchone()
        return self._row_to_binding(row) if row else None

    def bind(
        self,
        execution_id: str,
        path: OrchestrationPath,
    ) -> ExecutionPathBinding:
        candidate = ExecutionPathBinding.model_validate(
            {
                "execution_id": execution_id,
                "path": path,
                "bound_at": datetime.now(timezone.utc),
            }
        )
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (execution_id, orchestration_path, "
                        "schema_version) VALUES (%s,%s,%s) "
                        "ON CONFLICT (execution_id) DO NOTHING "
                        f"RETURNING {self._COLUMNS}"
                    ).format(table=sql.Identifier(self.table_name)),
                    (
                        candidate.execution_id,
                        candidate.path,
                        candidate.schema_version,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        sql.SQL(
                            f"SELECT {self._COLUMNS} FROM {{table}} "
                            "WHERE execution_id=%s"
                        ).format(table=sql.Identifier(self.table_name)),
                        (candidate.execution_id,),
                    )
                    row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("execution path binding was not persisted")
                binding = self._row_to_binding(row)
                if binding.path != path:
                    raise ExecutionPathConflict(
                        execution_id=execution_id,
                        existing_path=binding.path,
                        requested_path=path,
                    )
                return binding

    @staticmethod
    def _row_to_binding(row) -> ExecutionPathBinding:
        return ExecutionPathBinding(
            execution_id=row[0],
            path=row[1],
            bound_at=row[2],
            schema_version=row[3],
        )


__all__ = ["PostgresExecutionPathBindingStore"]
