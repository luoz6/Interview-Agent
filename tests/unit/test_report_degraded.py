import pytest

from app.services.report import DimensionScores
from app.services.report_artifact import PublishReportArtifact
from app.services.report_degraded import (
    DEGRADED_REPORT_TEMPLATE_VERSION,
    build_degraded_report_from_feedbacks,
    completed_feedbacks_in_manifest_order,
)
from tests.report_microbatch_fixtures import completed_record, make_feedback


def _insufficient_feedback(question_id: str):
    return make_feedback(question_id=question_id, score=70).model_copy(
        update={
            "score": None,
            "dimension_scores": DimensionScores(),
            "evaluation_status": "insufficient_evidence",
            "evaluation_reason_code": "evidence_extraction_failed",
            "evidence_count": 0,
            "dimension_evidence": [],
        }
    )


def test_summary_failure_publishes_degraded_report_without_erasing_valid_scores():
    report = build_degraded_report_from_feedbacks(
        session_id="session-degraded",
        feedbacks=[
            make_feedback(question_id="q1", score=78),
            make_feedback(question_id="q2", score=82),
        ],
        failed_components=["summary"],
        source_failure_code="provider_timeout",
    )

    assert report.generation_status == "degraded"
    assert report.generation_reason_code == "summary_generation_failed"
    assert report.score_status == "scored"
    assert report.overall_score == 80
    assert [item.score for item in report.feedbacks] == [78, 82]
    assert report.coverage_status == "complete"
    assert report.technical_appendix.summary_generation_mode == (
        "deterministic_fallback"
    )
    assert report.technical_appendix.metadata["degraded_template_version"] == (
        DEGRADED_REPORT_TEMPLATE_VERSION
    )
    assert report.technical_appendix.metadata["provider_analysis_completed"] is False
    assert report.technical_appendix.metadata["score_state_preserved"] is True
    assert "已确定：" in report.summary
    assert "未生成：" in report.summary
    assert any(
        item.reason_code == "summary_generation_failed"
        for item in report.limitations
    )


def test_partial_scores_keep_their_numerator_and_denominator_when_text_degrades():
    report = build_degraded_report_from_feedbacks(
        session_id="session-partial",
        feedbacks=[
            make_feedback(question_id="q1", score=78),
            _insufficient_feedback("q2"),
        ],
        failed_components=["summary"],
        source_failure_code="provider_unavailable",
    )

    assert report.generation_status == "degraded"
    assert report.score_status == "partial"
    assert report.coverage_status == "partial"
    assert report.overall_score == 78
    assert report.evaluated_count == 1
    assert report.total_eligible_count == 2
    assert report.feedbacks[1].score is None
    assert "部分逐题评价" in report.summary


def test_insufficient_evidence_publishes_unscored_without_any_numeric_score():
    report = build_degraded_report_from_feedbacks(
        session_id="session-unscored",
        feedbacks=[_insufficient_feedback("q1")],
        failed_components=["summary"],
        source_failure_code="invalid_provider_output",
    )

    assert report.generation_status == "degraded"
    assert report.score_status == "unscored"
    assert report.coverage_status == "none"
    assert report.overall_score is None
    assert all(
        value is None
        for value in report.overall_dimension_scores.model_dump().values()
    )
    assert report.feedbacks[0].score is None
    assert all(
        value is None
        for value in report.feedbacks[0].dimension_scores.model_dump().values()
    )
    assert "证据不足：" in report.summary
    assert "数字分保持为空" in report.summary


def test_action_failure_uses_deterministic_observation_bound_templates():
    report = build_degraded_report_from_feedbacks(
        session_id="session-action",
        feedbacks=[make_feedback(question_id="q1", score=78)],
        failed_components=["action"],
        source_failure_code="provider_unavailable",
    )

    assert report.generation_reason_code == "summary_generation_failed"
    assert report.technical_appendix.summary_generation_mode == "deterministic"
    assert report.technical_appendix.metadata["degraded_components"] == ["action"]
    assert "模型行动建议不可用" in report.summary
    assert all(action.observation_refs for action in report.priority_actions)
    assert all(action.evidence_refs for action in report.priority_actions)
    assert all(
        evidence_ref in {item.evidence_ref_id for item in report.evidence_refs}
        for action in report.priority_actions
        for evidence_ref in action.evidence_refs
    )
    publish = PublishReportArtifact(
        schema_version=report.report_schema_version,
        scoring_rubric_version=report.scoring_rubric_version,
        generation_status=report.generation_status,
        generation_reason_code=report.generation_reason_code,
        score_status=report.score_status,
        score_reason_code=report.score_reason_code,
        coverage_status=report.coverage_status,
        report_path=report.report_path,
        payload=report.model_dump(mode="json"),
    )
    assert publish.generation_reason_code == "summary_generation_failed"


def test_combined_text_failure_records_both_components_without_claiming_completion():
    report = build_degraded_report_from_feedbacks(
        session_id="session-combined",
        feedbacks=[make_feedback(question_id="q1", score=78)],
        failed_components=["summary", "action", "summary"],
        source_failure_code="provider_timeout",
    )

    assert report.generation_reason_code == "summary_generation_failed"
    assert report.technical_appendix.metadata["degraded_components"] == [
        "action",
        "summary",
    ]
    assert report.summary.count("未生成：") == 2
    assert report.technical_appendix.metadata["provider_analysis_completed"] is False


def test_degraded_builder_rejects_missing_or_unknown_failure_boundaries():
    feedbacks = [make_feedback(question_id="q1", score=78)]

    with pytest.raises(ValueError, match="at least one failed component"):
        build_degraded_report_from_feedbacks(
            session_id="session-invalid",
            feedbacks=feedbacks,
            failed_components=[],
            source_failure_code="provider_timeout",
        )
    with pytest.raises(ValueError, match="unsupported degraded report component"):
        build_degraded_report_from_feedbacks(
            session_id="session-invalid",
            feedbacks=feedbacks,
            failed_components=["scoring"],
            source_failure_code="provider_timeout",
        )
    with pytest.raises(ValueError, match="feedbacks must not be empty"):
        build_degraded_report_from_feedbacks(
            session_id="session-invalid",
            feedbacks=[],
            failed_components=["summary"],
            source_failure_code="provider_timeout",
        )


def test_completed_feedbacks_are_closed_and_sorted_by_frozen_manifest():
    records = [
        completed_record("s1", "q2", 82),
        completed_record("s1", "q1", 78),
    ]

    feedbacks = completed_feedbacks_in_manifest_order(
        records,
        expected_question_ids=["q1", "q2"],
    )

    assert [feedback.question_id for feedback in feedbacks] == [
        "q1",
        "q2",
    ]


def test_completed_feedback_closure_rejects_missing_extra_or_duplicate_records():
    first = completed_record("s1", "q1", 78)
    second = completed_record("s1", "q2", 82)

    with pytest.raises(ValueError, match="all question evaluations"):
        completed_feedbacks_in_manifest_order(
            [first],
            expected_question_ids=["q1", "q2"],
        )
    with pytest.raises(ValueError, match="all question evaluations"):
        completed_feedbacks_in_manifest_order(
            [first, second],
            expected_question_ids=["q1"],
        )
    with pytest.raises(ValueError, match="duplicate completed"):
        completed_feedbacks_in_manifest_order(
            [first, first],
            expected_question_ids=["q1"],
        )
