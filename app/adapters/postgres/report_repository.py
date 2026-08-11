from __future__ import annotations

import json
from typing import Any

from app.adapters.postgres.row_mappers import ReportRowMapper
from app.adapters.postgres.session_repository_support import (
    iso_timestamp,
    postgres_sql,
)
from app.services.postgres_connections import ConnectionProvider
from app.services.prep import InterviewPlan
from app.services.report import ReportRecord


_postgres_sql = postgres_sql


class PostgresReportRepository:
    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        sessions_table: str,
        messages_table: str,
        reports_table: str,
    ) -> None:
        self._connection_provider = connection_provider
        self.sessions_table = sessions_table
        self.messages_table = messages_table
        self.reports_table = reports_table

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        sql = _postgres_sql()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT status, progress_json, report_json, error,
                               created_at, completed_at, failed_at,
                               row_schema_version
                        FROM {reports}
                        WHERE session_id = %s
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (session_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return ReportRowMapper.from_row(
            {
                "status": row[0],
                "progress_json": row[1],
                "report_json": row[2],
                "error": row[3],
                "created_at": iso_timestamp(row[4]),
                "finished_at": iso_timestamp(row[5] or row[6]),
                "row_schema_version": row[7],
            }
        )

    def upsert_report_record(
        self,
        cursor: Any,
        session_id: str,
        record: ReportRecord,
    ) -> None:
        sql = _postgres_sql()
        row = ReportRowMapper.to_row(record)
        completed_finished_at = (
            row["finished_at"] if row["status"] == "completed" else None
        )
        failed_finished_at = (
            row["finished_at"] if row["status"] == "failed" else None
        )
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {reports} (
                    session_id, status, progress_json, report_json, error,
                    created_at, completed_at, failed_at
                    , row_schema_version
                )
                VALUES (
                    %s, %s, %s::jsonb, %s::jsonb, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (session_id) DO UPDATE
                SET status = EXCLUDED.status,
                    progress_json = EXCLUDED.progress_json,
                    report_json = EXCLUDED.report_json,
                    error = EXCLUDED.error,
                    updated_at = NOW(),
                    completed_at = CASE
                        WHEN EXCLUDED.status = 'completed' THEN EXCLUDED.completed_at
                        ELSE {reports}.completed_at
                    END,
                    failed_at = CASE
                        WHEN EXCLUDED.status = 'failed' THEN EXCLUDED.failed_at
                        ELSE {reports}.failed_at
                    END,
                    row_schema_version = EXCLUDED.row_schema_version
                """
            ).format(reports=sql.Identifier(self.reports_table)),
            (
                session_id,
                row["status"],
                json.dumps(row["progress_json"], ensure_ascii=False)
                if row["progress_json"] is not None
                else None,
                json.dumps(row["report_json"], ensure_ascii=False)
                if row["report_json"] is not None
                else None,
                row["error"],
                row["created_at"],
                completed_finished_at,
                failed_finished_at,
                row["row_schema_version"],
            ),
        )

    def list_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = _postgres_sql()
        where_clause, params = self._report_filter_clause(
            sql,
            status=status,
            query=query,
            days=days,
        )
        page_params = [*params, limit, offset]
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT reports.session_id, reports.status,
                               reports.progress_json, reports.report_json,
                               reports.error, reports.created_at,
                               reports.completed_at, reports.failed_at,
                               sessions.plan_json, sessions.job_tags,
                               sessions.started_at, sessions.finished_at,
                               COALESCE(answer_counts.answered_question_count, 0),
                               reports.row_schema_version
                        FROM {reports} AS reports
                        LEFT JOIN {sessions} AS sessions
                          ON sessions.session_id = reports.session_id
                        LEFT JOIN (
                            SELECT session_id,
                                   COUNT(DISTINCT question_id) AS answered_question_count
                            FROM {messages}
                            WHERE role = 'candidate'
                              AND question_id IS NOT NULL
                              AND BTRIM(content) <> ''
                            GROUP BY session_id
                        ) AS answer_counts
                          ON answer_counts.session_id = reports.session_id
                        {where_clause}
                        ORDER BY reports.created_at DESC, reports.session_id DESC
                        LIMIT %s OFFSET %s
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                        messages=sql.Identifier(self.messages_table),
                        where_clause=where_clause,
                    ),
                    tuple(page_params),
                )
                rows = cursor.fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            plan = InterviewPlan.model_validate(row[8])
            items.append(
                {
                    "session_id": row[0],
                    "record": ReportRowMapper.from_row(
                        {
                            "status": row[1],
                            "progress_json": row[2],
                            "report_json": row[3],
                            "error": row[4],
                            "created_at": iso_timestamp(row[5]),
                            "finished_at": iso_timestamp(row[6] or row[7]),
                            "row_schema_version": row[13],
                        }
                    ),
                    "session_summary": {
                        "job_title": plan.title,
                        "job_tags": list(row[9]),
                        "question_count": len(plan.questions),
                        "answered_question_count": row[12],
                        "started_at": iso_timestamp(row[10]),
                        "finished_at": iso_timestamp(row[11]),
                    },
                }
            )
        return items

    def count_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
    ) -> int:
        sql = _postgres_sql()
        where_clause, params = self._report_filter_clause(
            sql,
            status=status,
            query=query,
            days=days,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT COUNT(*)
                        FROM {reports} AS reports
                        LEFT JOIN {sessions} AS sessions
                          ON sessions.session_id = reports.session_id
                        {where_clause}
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                        where_clause=where_clause,
                    ),
                    tuple(params),
                )
                row = cursor.fetchone()
        return int(row[0])

    def report_status_totals(
        self,
        *,
        query: str | None = None,
        days: int | None = None,
    ) -> dict[str, int]:
        sql = _postgres_sql()
        where_clause, params = self._report_filter_clause(
            sql,
            status=None,
            query=query,
            days=days,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT reports.status, COUNT(*)
                        FROM {reports} AS reports
                        LEFT JOIN {sessions} AS sessions
                          ON sessions.session_id = reports.session_id
                        {where_clause}
                        GROUP BY reports.status
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                        where_clause=where_clause,
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall()
        totals = {"all": 0, "processing": 0, "completed": 0, "failed": 0}
        for status, count in rows:
            safe_count = int(count)
            totals["all"] += safe_count
            if status in totals:
                totals[status] = safe_count
        return totals

    @staticmethod
    def _report_filter_clause(sql, *, status, query, days):
        clauses = []
        params: list[Any] = []
        if status is not None:
            clauses.append(sql.SQL("reports.status = %s"))
            params.append(status)
        normalized_query = (query or "").strip()
        if normalized_query:
            clauses.append(
                sql.SQL(
                    """
                    (
                        reports.session_id ILIKE %s
                        OR COALESCE(sessions.plan_json ->> 'title', '') ILIKE %s
                        OR COALESCE(sessions.job_tags::text, '') ILIKE %s
                        OR COALESCE(reports.report_json ->> 'summary', '') ILIKE %s
                        OR reports.status ILIKE %s
                    )
                    """
                )
            )
            pattern = f"%{normalized_query}%"
            params.extend([pattern] * 5)
        if days is not None:
            clauses.append(
                sql.SQL(
                    "COALESCE(reports.completed_at, reports.failed_at, "
                    "reports.created_at) >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')"
                )
            )
            params.append(days)
        if not clauses:
            return sql.SQL(""), params
        return sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses), params


__all__ = ["PostgresReportRepository"]
