from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.adapters.postgres.row_mappers.errors import require_supported_row_version


PREP_PLAN_ROW_SCHEMA_VERSION = "prep-plan-row-v1"
PREP_PLAN_VERSION_ROW_SCHEMA_VERSION = "prep-plan-version-row-v1"


class PrepPlanRowMapper:
    CURRENT_VERSION = PREP_PLAN_ROW_SCHEMA_VERSION
    VERSION_CURRENT_VERSION = PREP_PLAN_VERSION_ROW_SCHEMA_VERSION
    BACKFILL_POLICY = "missing-columns-mean-corresponding-v1-row"

    @staticmethod
    def internal_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "internal_plan": record["internal_plan"],
            "question_contexts": record.get("question_contexts") or {},
            "context_catalog": record.get("context_catalog") or {},
            "job_description": record["job_description"],
            "resume_text": record["resume_text"],
            "job_tags": record["job_tags"],
            "practice_provenance": record.get("practice_provenance"),
        }

    @classmethod
    def record_to_row(cls, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "public": record["public"],
            "internal": cls.internal_payload(record),
            "row_schema_version": cls.CURRENT_VERSION,
        }

    @classmethod
    def record_from_db_row(cls, row: Any) -> dict[str, Any]:
        require_supported_row_version(
            row[13] if len(row) > 13 else None,
            row_type="prep plan",
            current_version=cls.CURRENT_VERSION,
        )
        public = dict(row[3])
        internal = dict(row[4])
        return {
            "public": public,
            "internal_plan": internal["internal_plan"],
            "question_contexts": dict(internal.get("question_contexts") or {}),
            "context_catalog": dict(internal.get("context_catalog") or {}),
            "job_description": internal["job_description"],
            "resume_text": internal["resume_text"],
            "job_tags": list(internal.get("job_tags") or []),
            "practice_provenance": deepcopy(
                internal.get("practice_provenance")
            ),
            "source_sha256": row[5],
            "source_draft_id": row[6],
            "expires_at": row[7].isoformat(),
            "state": row[2],
            "consumed_session_id": row[8],
            "consumed_command_id": row[9],
            "consumed_plan_version": row[10],
            "created_at": row[11].isoformat(),
            "updated_at": row[12].isoformat(),
        }

    @classmethod
    def version_to_row(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            **snapshot,
            "row_schema_version": cls.VERSION_CURRENT_VERSION,
        }

    @classmethod
    def validate_version_row(cls, row: dict[str, Any]) -> None:
        require_supported_row_version(
            row.get("row_schema_version"),
            row_type="prep plan version",
            current_version=cls.VERSION_CURRENT_VERSION,
        )
