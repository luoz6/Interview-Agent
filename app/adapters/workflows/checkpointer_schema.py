from __future__ import annotations

from typing import Any

from app.adapters.postgres.connections import PostgresSchemaNotReady


CHECKPOINTER_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


def validate_checkpointer_schema(pool: Any) -> None:
    """Read-only adapter gate for tables created by the migration command."""

    with pool.connection(timeout=2.0) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('public.' || name) "
                "FROM unnest(%s::text[]) AS name",
                (list(CHECKPOINTER_TABLES),),
            )
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = ANY(%s::text[])",
                (list(CHECKPOINTER_TABLES),),
            )
            column_rows = cursor.fetchall()
            cursor.execute("SELECT MAX(v) FROM checkpoint_migrations")
            migration_row = cursor.fetchone()

    present = []
    for row in rows:
        if isinstance(row, dict):
            present.append(next(iter(row.values())))
        else:
            present.append(row[0])
    if len(present) != len(CHECKPOINTER_TABLES) or any(
        value is None for value in present
    ):
        raise PostgresSchemaNotReady(
            "LangGraph Checkpointer schema is not ready"
        )
    required_columns = {
        "checkpoint_migrations": {"v"},
        "checkpoints": {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "checkpoint",
            "metadata",
        },
        "checkpoint_blobs": {
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
            "type",
            "blob",
        },
        "checkpoint_writes": {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "task_path",
            "idx",
            "channel",
            "blob",
        },
    }
    columns = {name: set() for name in CHECKPOINTER_TABLES}
    for row in column_rows:
        if isinstance(row, dict):
            values = list(row.values())
            table_name, column_name = values[0], values[1]
        else:
            table_name, column_name = row[0], row[1]
        columns.setdefault(table_name, set()).add(column_name)
    if any(
        not required.issubset(columns.get(table_name, set()))
        for table_name, required in required_columns.items()
    ):
        raise PostgresSchemaNotReady(
            "LangGraph Checkpointer schema is incompatible"
        )
    from langgraph.checkpoint.postgres import PostgresSaver

    latest_expected = len(PostgresSaver.MIGRATIONS) - 1
    latest_applied = (
        next(iter(migration_row.values()))
        if isinstance(migration_row, dict)
        else migration_row[0]
    )
    if latest_applied is None or int(latest_applied) < latest_expected:
        raise PostgresSchemaNotReady(
            "LangGraph Checkpointer migration is incomplete"
        )
