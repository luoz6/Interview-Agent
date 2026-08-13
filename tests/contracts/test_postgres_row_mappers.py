from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.knowledge.evidence import (
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceDecision,
    EvidenceRef,
    EvidenceSufficiency,
    ReviewEvidenceBinding,
)
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


def test_question_evaluation_mapper_round_trips_full_review_binding():
    supplemental_reference = EvidenceRef(
        evidence_id="redis-2",
        title="Redis fencing",
        domain="redis",
        source_type="expert_benchmark",
        content_sha256="c" * 64,
        corpus_manifest_sha256="d" * 64,
        corpus_version="memory-p1-zh-v3",
        authority_metadata={"status": "schema_validated"},
        provenance={"source_path": "expert/redis.md"},
    )
    binding = ReviewEvidenceBinding(
        binding_id="review-binding-1",
        parent_question_binding_id="question-binding-1",
        replayed_evidence_ids=("redis-1",),
        supplemental_evidence_ids=("redis-2",),
        supplemental_evidence_refs=(supplemental_reference,),
        final_evidence_ids=("redis-1", "redis-2"),
        decision=EvidenceDecision(
            availability=EvidenceAvailability.AVAILABLE,
            sufficiency=EvidenceSufficiency.NOT_EVALUATED,
            evaluation_confidence=EvaluationConfidence.NOT_SCORABLE,
            gate_version="retrieval-gate-v1",
        ),
    )
    record = QuestionEvaluationRecord(
        session_id="session-1",
        question_id="question-1",
        status="failed",
        error="review failed after evidence resolution",
        evidence_binding_id=binding.binding_id,
        review_evidence_binding=binding,
    )

    restored = QuestionEvaluationRowMapper.from_row(
        QuestionEvaluationRowMapper.to_row(record)
    )

    assert restored.evidence_binding_id == binding.binding_id
    assert restored.review_evidence_binding == binding
    assert restored.review_evidence_binding.supplemental_evidence_refs == (
        supplemental_reference,
    )
