from datetime import datetime, timezone

import pytest

from app.services.report_artifact import ReportArtifact
from app.services.report_view import (
    EvaluationView,
    ReportViewError,
    ReportViewModel,
    compose_report_view,
)


def artifact(*, score_status="scored", payload=None, coverage_status="complete"):
    from app.services.report_artifact import report_artifact_sha256

    if payload is None:
        payload = {
            "overall_score": 84,
            "overall_dimension_scores": {"depth": 84},
        }
    return ReportArtifact(
        report_id="11111111-1111-4111-8111-111111111111",
        session_id="session-1",
        revision=1,
        schema_version="report-artifact-v2",
        scoring_rubric_version="rubric-v1",
        generation_status="complete",
        generation_reason_code="normal",
        score_status=score_status,
        score_reason_code="sufficient_evidence" if score_status == "scored" else "insufficient_evidence",
        coverage_status=coverage_status,
        report_path="full_session",
        payload=payload,
        artifact_sha256=report_artifact_sha256(payload),
        source_job_id="22222222-2222-4222-8222-222222222222",
        created_at=datetime.now(timezone.utc),
    )


def test_unscored_cannot_expose_numeric_scores():
    with pytest.raises(ValueError, match="unscored"):
        compose_report_view(
            artifact(
                score_status="unscored",
                coverage_status="none",
                payload={"overall_score": 1, "overall_dimension_scores": {"depth": 1}},
            )
        )


def test_partial_requires_coverage_denominator():
    with pytest.raises(ValueError, match="total_eligible_count|denominator"):
        ReportViewModel(
            report_id="11111111-1111-4111-8111-111111111111",
            session_id="session-1",
            revision=1,
            artifact_sha256="a" * 64,
            source_job_id="22222222-2222-4222-8222-222222222222",
            active=True,
            schema_version="report-artifact-v2",
            generation_status="complete",
            generation_reason_code="normal",
            score_status="partial",
            score_reason_code="partial_evidence",
            coverage_status="partial",
            report_path="full_session",
            overall_score=50,
            payload={},
        )


def test_legacy_payload_does_not_fabricate_coverage_for_missing_score():
    legacy = artifact(
        payload={"summary": "legacy"},
    ).model_copy(update={"schema_version": "legacy-v1"})
    view = compose_report_view(legacy)
    assert view.score_status == "unscored"
    assert view.coverage_status == "none"
    assert view.overall_score is None
    assert view.report_schema_version == "report-schema-v1"
    assert view.presentation_version == "report-presentation-v1"


def test_v2_payload_versions_are_projected_independently_from_artifact_envelope():
    view = compose_report_view(
        artifact(
            payload={
                "report_schema_version": "report-schema-v2",
                "presentation_version": "report-presentation-v9",
                "scoring_rubric_version": "rubric-v11",
                "overall_score": 84,
                "overall_dimension_scores": {"depth": 84},
            }
        )
    )

    assert view.schema_version == "report-artifact-v2"
    assert view.report_schema_version == "report-schema-v2"
    assert view.presentation_version == "report-presentation-v9"
    assert view.scoring_rubric_version == "rubric-v11"


def test_not_evaluated_dimension_cannot_use_zero_as_a_placeholder():
    with pytest.raises(ValueError, match="not_evaluated"):
        EvaluationView(status="not_evaluated", score=0)
