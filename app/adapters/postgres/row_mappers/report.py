from __future__ import annotations

from typing import Any

from app.adapters.postgres.row_mappers.errors import require_supported_row_version
from app.services.report import InterviewReport, ReportProgress, ReportRecord


REPORT_ROW_SCHEMA_VERSION = "report-row-v1"


class ReportRowMapper:
    CURRENT_VERSION = REPORT_ROW_SCHEMA_VERSION
    BACKFILL_POLICY = "missing-column-means-report-row-v1"

    @classmethod
    def to_row(cls, record: ReportRecord) -> dict[str, Any]:
        return {
            "status": record.status,
            "progress_json": record.progress.model_dump(mode="json")
            if record.progress is not None
            else None,
            "report_json": record.report.model_dump(mode="json")
            if record.report is not None
            else None,
            "error": record.error,
            "created_at": record.created_at,
            "finished_at": record.finished_at,
            "row_schema_version": cls.CURRENT_VERSION,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ReportRecord:
        require_supported_row_version(
            row.get("row_schema_version"),
            row_type="report",
            current_version=cls.CURRENT_VERSION,
        )
        progress = (
            ReportProgress.model_validate(row["progress_json"])
            if row.get("progress_json") is not None
            else None
        )
        report = (
            InterviewReport.model_validate(row["report_json"])
            if row.get("report_json") is not None
            else None
        )
        values = {
            "status": row["status"],
            "progress": progress,
            "report": report,
            "error": row.get("error"),
            "finished_at": row.get("finished_at"),
        }
        if row.get("created_at"):
            values["created_at"] = row["created_at"]
        return ReportRecord(**values)
