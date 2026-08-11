from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.adapters.postgres.row_mappers import (
    PrepPlanRowMapper,
    QuestionEvaluationRowMapper,
    UnsupportedRowSchemaVersionError,
)
from app.services.question_evaluations import QuestionEvaluationRecord


def _prep_record() -> dict:
    return {
        "public": {"plan_id": "plan-1", "plan_version": 1},
        "internal_plan": {"questions": []},
        "question_contexts": {},
        "context_catalog": {},
        "job_description": "Backend role",
        "resume_text": "Built APIs",
        "job_tags": ["python"],
        "practice_provenance": None,
    }


def _prep_db_row(version: str | None = PrepPlanRowMapper.CURRENT_VERSION):
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    return (
        "plan-1",
        1,
        "editable",
        {"plan_id": "plan-1", "plan_version": 1},
        PrepPlanRowMapper.internal_payload(_prep_record()),
        "a" * 64,
        None,
        now,
        None,
        None,
        None,
        now,
        now,
        version,
    )


def test_prep_plan_mapper_persists_record_and_version_schema_versions():
    mapped = PrepPlanRowMapper.record_to_row(_prep_record())
    version = PrepPlanRowMapper.version_to_row(
        {
            "plan_id": "plan-1",
            "version": 1,
            "public_snapshot": {"plan_id": "plan-1"},
            "change_type": "created",
            "replaced_question_id": None,
            "replacement_question_id": None,
        }
    )

    assert mapped["row_schema_version"] == "prep-plan-row-v1"
    assert version["row_schema_version"] == "prep-plan-version-row-v1"
    assert PrepPlanRowMapper.record_from_db_row(_prep_db_row())["job_tags"] == [
        "python"
    ]


def test_prep_plan_mapper_backfills_missing_version_and_rejects_unknown():
    legacy = _prep_db_row()[:-1]
    assert PrepPlanRowMapper.record_from_db_row(legacy)["state"] == "editable"

    with pytest.raises(UnsupportedRowSchemaVersionError, match="prep plan"):
        PrepPlanRowMapper.record_from_db_row(_prep_db_row("future-v9"))


def test_question_evaluation_mapper_rejects_unknown_row_version():
    record = QuestionEvaluationRecord(
        session_id="session-1",
        question_id="question-1",
        answer_state="unanswered",
        status="failed",
        error="not answered",
    )
    row = QuestionEvaluationRowMapper.to_row(record)
    row["row_schema_version"] = "future-v9"

    with pytest.raises(
        UnsupportedRowSchemaVersionError,
        match="question evaluation",
    ):
        QuestionEvaluationRowMapper.from_row(row)
