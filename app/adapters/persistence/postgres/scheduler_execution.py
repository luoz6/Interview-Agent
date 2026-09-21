from __future__ import annotations

import json
from typing import Any

from app.adapters.postgres.session_repository_support import postgres_sql
from app.domain.interview.scheduling import (
    ExecutionPlan,
    ExecutionState,
    ExecutionStateConflict,
)


class PostgresSchedulerExecutionRepository:
    """PostgreSQL plan/state repository with revision-fenced state updates."""

    def __init__(self, connection_provider: Any, *, table_name: str) -> None:
        self._provider = connection_provider
        self.table_name = table_name

    def create(self, plan: ExecutionPlan, state: ExecutionState) -> None:
        if plan.execution_id != state.execution_id:
            raise ValueError("plan and state execution identities differ")
        sql = postgres_sql()
        plan_json = plan.model_dump(mode="json")
        state_json = state.model_dump(mode="json")
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (execution_id, plan_json, state_json, state_revision) "
                        "VALUES (%s, %s::jsonb, %s::jsonb, %s) "
                        "ON CONFLICT (execution_id) DO NOTHING"
                    ).format(table=sql.Identifier(self.table_name)),
                    (
                        plan.execution_id,
                        json.dumps(plan_json, ensure_ascii=False),
                        json.dumps(state_json, ensure_ascii=False),
                        state.revision,
                    ),
                )
                inserted = cursor.rowcount == 1
                if not inserted:
                    cursor.execute(
                        sql.SQL(
                            "SELECT plan_json, state_json FROM {table} WHERE execution_id = %s"
                        ).format(table=sql.Identifier(self.table_name)),
                        (plan.execution_id,),
                    )
                    row = cursor.fetchone()
                    if row is None or ExecutionPlan.model_validate(row[0]) != plan or ExecutionState.model_validate(row[1]) != state:
                        raise RuntimeError(
                            "Scheduler execution already exists with other data"
                        )
            connection.commit()

    def load_plan(self, execution_id: str) -> ExecutionPlan:
        row = self._select(execution_id, "plan_json")
        return ExecutionPlan.model_validate(row)

    def load(self, execution_id: str) -> ExecutionState:
        row = self._select(execution_id, "state_json")
        return ExecutionState.model_validate(row)

    def save(self, state: ExecutionState) -> ExecutionState:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT state_json, state_revision FROM {table} "
                        "WHERE execution_id = %s FOR UPDATE"
                    ).format(table=sql.Identifier(self.table_name)),
                    (state.execution_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(
                        f"execution state not found: {state.execution_id}"
                    )
                current_state = ExecutionState.model_validate(row[0])
                current_revision = int(row[1])
                if current_state == state:
                    return state
                if state.revision <= current_revision:
                    raise ExecutionStateConflict(
                        expected_revision=state.revision,
                        actual_revision=current_revision,
                    )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET state_json = %s::jsonb, state_revision = %s, "
                        "updated_at = NOW() WHERE execution_id = %s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (
                        json.dumps(state.model_dump(mode="json"), ensure_ascii=False),
                        state.revision,
                        state.execution_id,
                    ),
                )
            connection.commit()
        return state

    def delete(self, execution_id: str) -> int:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {table} WHERE execution_id = %s").format(
                        table=sql.Identifier(self.table_name)
                    ),
                    (execution_id,),
                )
                deleted = cursor.rowcount
            connection.commit()
        return int(deleted)

    def _select(self, execution_id: str, column: str):
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT {column} FROM {table} WHERE execution_id = %s").format(
                        column=sql.Identifier(column),
                        table=sql.Identifier(self.table_name),
                    ),
                    (execution_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Scheduler execution not found: {execution_id}")
        return row[0]


__all__ = ["PostgresSchedulerExecutionRepository"]
