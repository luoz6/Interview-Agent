import pytest
from pydantic import ValidationError

from app.services.report import (
    DimensionScores,
    InterviewReport,
    REPORT_DIMENSIONS,
    REPORT_PRESENTATION_VERSION_V1,
    REPORT_PRESENTATION_VERSION_V2,
    REPORT_SCHEMA_VERSION_V1,
    REPORT_SCHEMA_VERSION_V2,
)
from app.services.report_contract import (
    CanonicalQuestionResult,
    assemble_interview_report,
)
from app.services.report_view import (
    DEFAULT_REPORT_PAYLOAD_PARSERS,
    EvaluationView,
)


def _report():
    return assemble_interview_report(
        session_id="session-1",
        question_results=[
            CanonicalQuestionResult(
                question_id="q1",
                question_text="Explain cache consistency.",
                user_answer="I update the database and then invalidate the cache.",
                score=80,
                dimension_scores=DimensionScores(
                    breadth=78,
                    depth=82,
                    architecture=80,
                    engineering=84,
                    communication=76,
                ),
                applicable_dimensions=list(REPORT_DIMENSIONS),
                rationale="The answer states an ordering and a consistency goal.",
                critique="It does not explain failure recovery.",
                better_answer="Add rollback, retry, and observability details.",
                reference_chunk_ids=["reference-1"],
                highlights=["Explained the cache invalidation order"],
            )
        ],
        reference_lookup={
            "reference-1": {
                "chunk_id": "reference-1",
                "title": "Cache consistency",
                "source_type": "theory",
                "excerpt": "Invalidate after the database commit.",
            }
        },
    )


def test_new_reports_publish_independent_v2_semantic_fields():
    report = _report()

    assert report.report_schema_version == REPORT_SCHEMA_VERSION_V2
    assert report.presentation_version == REPORT_PRESENTATION_VERSION_V2
    assert report.scoring_rubric_version
    assert report.coverage is not None
    assert report.coverage.status == report.coverage_status
    assert set(report.coverage.per_dimension) == set(REPORT_DIMENSIONS)
    assert report.summary_observations == []
    assert report.strengths == []
    assert report.priority_actions == []
    assert report.limitations == []
    assert {item.namespace for item in report.evidence_refs} == {
        "candidate",
        "reference",
    }
    assert report.technical_appendix.report_path == "full_session"


def test_v2_claims_and_actions_must_reference_published_evidence():
    payload = _report().model_dump(mode="json")
    payload["summary_observations"] = [
        {
            "claim_id": "summary-1",
            "text": "The answer explained cache invalidation ordering.",
            "evidence_refs": ["candidate:q1:answer"],
        }
    ]
    payload["strengths"] = [
        {
            "claim_id": "strength-1",
            "text": "The response named a deterministic update order.",
            "evidence_refs": ["candidate:q1:answer"],
        }
    ]
    payload["priority_actions"] = [
        {
            "action_id": "action-1",
            "title": "Practice failure recovery",
            "why_it_matters": "The current answer stops at the happy path.",
            "practice": "Re-answer the same question with rollback and retry paths.",
            "completion_criteria": "Name the trigger, fallback, and verification metric.",
            "evidence_refs": ["candidate:q1:answer"],
        }
    ]

    validated = InterviewReport.model_validate(payload)
    assert validated.summary_observations[0].claim_id == "summary-1"
    assert validated.priority_actions[0].action_id == "action-1"

    payload["priority_actions"][0]["evidence_refs"] = ["candidate:q9:missing"]
    with pytest.raises(ValidationError, match="unknown evidence ref"):
        InterviewReport.model_validate(payload)


def test_text_generation_failure_does_not_change_determined_score_state():
    payload = _report().model_dump(mode="json")
    original_score = payload["overall_score"]
    original_score_status = payload["score_status"]
    payload["generation_status"] = "degraded"
    payload["generation_reason_code"] = "summary_generation_failed"

    report = InterviewReport.model_validate(payload)

    assert report.generation_status == "degraded"
    assert report.overall_score == original_score
    assert report.score_status == original_score_status


def test_schema_presentation_and_rubric_versions_are_not_coupled():
    payload = _report().model_dump(mode="json")
    payload["presentation_version"] = REPORT_PRESENTATION_VERSION_V1
    payload["scoring_rubric_version"] = "rubric-independent-v99"

    report = InterviewReport.model_validate(payload)

    assert report.report_schema_version == REPORT_SCHEMA_VERSION_V2
    assert report.presentation_version == REPORT_PRESENTATION_VERSION_V1
    assert report.scoring_rubric_version == "rubric-independent-v99"


def test_legacy_artifact_payload_uses_v1_compatibility_without_upgrade():
    parsed = DEFAULT_REPORT_PAYLOAD_PARSERS.parse(
        "report-artifact-v2",
        {
            "summary": "Historical report",
            "overall_score": 70,
            "scoring_rubric_version": "legacy-rubric",
        },
    )

    assert parsed["report_schema_version"] == REPORT_SCHEMA_VERSION_V1
    assert parsed["presentation_version"] == REPORT_PRESENTATION_VERSION_V1
    assert "summary_observations" not in parsed
    assert "priority_actions" not in parsed


def test_public_view_preserves_insufficient_evidence_without_a_fake_score():
    evaluation = EvaluationView(
        status="insufficient_evidence",
        score=None,
        evidence_count=0,
        reason_code="insufficient_evidence",
    )
    assert evaluation.status == "insufficient_evidence"

    with pytest.raises(ValidationError, match="cannot contain a numeric score"):
        EvaluationView(
            status="insufficient_evidence",
            score=0,
            evidence_count=0,
            reason_code="insufficient_evidence",
        )
