from __future__ import annotations

from typing import Any

from app.adapters.postgres.row_mappers import MessageRowMapper
from app.adapters.postgres.session_repository_support import postgres_sql
from app.graphs.interview_state import InterviewState
from app.services.postgres_connections import ConnectionProvider


_postgres_sql = postgres_sql


class PostgresMessageRepository:
    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        messages_table: str,
    ) -> None:
        self._connection_provider = connection_provider
        self.messages_table = messages_table

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        sql = _postgres_sql()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT id, sequence_no, role, content, question_id,
                               row_schema_version
                        FROM {messages}
                        WHERE session_id = %s
                        ORDER BY sequence_no
                        """
                    ).format(messages=sql.Identifier(self.messages_table)),
                    (session_id,),
                )
                rows = cursor.fetchall()
        items = []
        for row in rows:
            message = MessageRowMapper.from_row(
                {
                    "role": row[2],
                    "content": row[3],
                    "question_id": row[4],
                    "row_schema_version": row[5],
                }
            )
            items.append({
                "id": row[0],
                "sequence_no": row[1],
                **message,
            })
        return items

    def insert_messages(self, cursor: Any, state: InterviewState) -> None:
        sql = _postgres_sql()
        for index, message in enumerate(state["messages"], start=1):
            message_row = MessageRowMapper.to_row(
                state["session_id"], index, message
            )
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {messages} (session_id, sequence_no, role, content, question_id, row_schema_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s)"
                ).format(messages=sql.Identifier(self.messages_table)),
                (
                    message_row["session_id"],
                    message_row["sequence_no"],
                    message_row["role"],
                    message_row["content"],
                    message_row["question_id"],
                    message_row["row_schema_version"],
                ),
            )

    def replace_messages(self, cursor: Any, state: InterviewState) -> None:
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                """
                SELECT sequence_no, role, content, question_id,
                       row_schema_version
                FROM {messages}
                WHERE session_id = %s
                ORDER BY sequence_no
                """
            ).format(messages=sql.Identifier(self.messages_table)),
            (state["session_id"],),
        )
        existing_messages = [
            {
                "sequence_no": row[0],
                "role": row[1],
                "content": row[2],
                "question_id": row[3],
                "row_schema_version": row[4],
            }
            for row in cursor.fetchall()
        ]
        new_message_rows = [
            MessageRowMapper.to_row(state["session_id"], index, message)
            for index, message in enumerate(state["messages"], start=1)
        ]

        common_prefix = 0
        for existing, new_row in zip(existing_messages, new_message_rows):
            if (
                existing["sequence_no"] == new_row["sequence_no"]
                and existing["role"] == new_row["role"]
                and existing["content"] == new_row["content"]
                and existing["question_id"] == new_row["question_id"]
                and existing["row_schema_version"]
                == new_row["row_schema_version"]
            ):
                common_prefix += 1
                continue
            break

        if common_prefix < len(existing_messages):
            cursor.execute(
                sql.SQL(
                    "DELETE FROM {messages} WHERE session_id = %s AND sequence_no > %s"
                ).format(messages=sql.Identifier(self.messages_table)),
                (state["session_id"], common_prefix),
            )
        for message_row in new_message_rows[common_prefix:]:
            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {messages} (
                        session_id, sequence_no, role, content, question_id
                        , row_schema_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """
                ).format(messages=sql.Identifier(self.messages_table)),
                (
                    message_row["session_id"],
                    message_row["sequence_no"],
                    message_row["role"],
                    message_row["content"],
                    message_row["question_id"],
                    message_row["row_schema_version"],
                ),
            )


__all__ = ["PostgresMessageRepository"]
