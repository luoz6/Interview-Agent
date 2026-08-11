from __future__ import annotations

import json
from typing import Any

from app.adapters.postgres.row_mappers import QuestionEvaluationRowMapper
from app.adapters.postgres.session_repository_support import (
    iso_timestamp,
    postgres_sql,
)
from app.services.postgres_connections import ConnectionProvider
from app.services.question_evaluations import (
    QuestionEvaluationInputConflict,
    QuestionEvaluationRecord,
)


_postgres_sql = postgres_sql


class PostgresQuestionEvaluationRepository:
    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        question_evaluations_table: str,
    ) -> None:
        self._connection_provider = connection_provider
        self.question_evaluations_table = question_evaluations_table

    def list_question_evaluations(
        self,
        session_id: str,
    ) -> list[QuestionEvaluationRecord]:
        sql = _postgres_sql()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, question_id, answer_state, status,
                               feedback_json, error, created_at,
                               review_input_sha256, question_input_sha256,
                               review_engine, review_graph_schema_version,
                               output_sha256, completed_at,
                               row_schema_version
                        FROM {question_evaluations}
                        WHERE session_id = %s
                        ORDER BY question_id
                        """
                    ).format(
                        question_evaluations=sql.Identifier(
                            self.question_evaluations_table
                        )
                    ),
                    (session_id,),
                )
                rows = cursor.fetchall()
        return [
            QuestionEvaluationRowMapper.from_row(
                {
                    "session_id": row[0],
                    "question_id": row[1],
                    "answer_state": row[2],
                    "status": row[3],
                    "feedback_json": row[4],
                    "error": row[5],
                    "created_at": iso_timestamp(row[6]),
                    "review_input_sha256": row[7],
                    "question_input_sha256": row[8],
                    "review_engine": row[9],
                    "review_graph_schema_version": row[10],
                    "output_sha256": row[11],
                    "completed_at": iso_timestamp(row[12]),
                    "row_schema_version": row[13],
                }
            )
            for row in rows
        ]

    def upsert_question_evaluation(
        self,
        cursor: Any,
        record: QuestionEvaluationRecord,
    ) -> None:
        sql = _postgres_sql()
        row = QuestionEvaluationRowMapper.to_row(record)
        if row["review_engine"] is not None:
            cursor.execute(
                sql.SQL(
                    """
                    SELECT review_engine, question_input_sha256
                    FROM {question_evaluations}
                    WHERE session_id = %s AND question_id = %s
                    FOR UPDATE
                    """
                ).format(
                    question_evaluations=sql.Identifier(
                        self.question_evaluations_table
                    )
                ),
                (row["session_id"], row["question_id"]),
            )
            existing = cursor.fetchone()
            if (
                existing is not None
                and existing[0] is not None
                and existing[1] != row["question_input_sha256"]
            ):
                raise QuestionEvaluationInputConflict(
                    f"question evaluation input changed: {row['question_id']}"
                )
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {question_evaluations} (
                    session_id, question_id, answer_state, status,
                    feedback_json, error, created_at,
                    review_input_sha256, question_input_sha256,
                    review_engine, review_graph_schema_version,
                    output_sha256, completed_at, row_schema_version
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, question_id) DO UPDATE
                SET status = EXCLUDED.status,
                    answer_state = EXCLUDED.answer_state,
                    feedback_json = EXCLUDED.feedback_json,
                    error = EXCLUDED.error,
                    review_input_sha256 = EXCLUDED.review_input_sha256,
                    question_input_sha256 = EXCLUDED.question_input_sha256,
                    review_engine = EXCLUDED.review_engine,
                    review_graph_schema_version = EXCLUDED.review_graph_schema_version,
                    output_sha256 = EXCLUDED.output_sha256,
                    completed_at = EXCLUDED.completed_at,
                    row_schema_version = EXCLUDED.row_schema_version,
                    updated_at = NOW()
                """
            ).format(
                question_evaluations=sql.Identifier(
                    self.question_evaluations_table
                )
            ),
            (
                row["session_id"],
                row["question_id"],
                row["answer_state"],
                row["status"],
                json.dumps(row["feedback_json"], ensure_ascii=False)
                if row["feedback_json"] is not None
                else None,
                row["error"],
                row["created_at"],
                row["review_input_sha256"],
                row["question_input_sha256"],
                row["review_engine"],
                row["review_graph_schema_version"],
                row["output_sha256"],
                row["completed_at"],
                row["row_schema_version"],
            ),
        )


__all__ = ["PostgresQuestionEvaluationRepository"]
