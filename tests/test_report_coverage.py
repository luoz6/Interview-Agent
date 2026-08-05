from pathlib import Path

from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.report_coverage import (
    aggregate_report_coverage,
    apply_report_coverage,
)


def feedback(
    question_id: str,
    *,
    answer_state: str = "answered",
    score: int | None = 80,
    dimensions: DimensionScores | None = None,
    evaluation_status: str = "evaluated",
    evaluation_reason_code: str = "sufficient_evidence",
    applicable_dimensions: list[str] | None = None,
    observed: dict[str, list[str]] | None = None,
) -> InterviewFeedback:
    evidence = [
        {
            "dimension": dimension,
            "observed": excerpts,
            "missing": [],
            "quality_signals": [],
        }
        for dimension, excerpts in (observed or {}).items()
    ]
    return InterviewFeedback(
        question_id=question_id,
        question_text=f"Question {question_id}",
        user_answer=(
            "候选人给出了可验证回答。"
            if answer_state == "answered"
            else "候选人没有提供可评估回答。"
        ),
        answer_state=answer_state,
        score=score,
        dimension_scores=dimensions or DimensionScores(),
        evaluation_status=evaluation_status,
        evaluation_reason_code=evaluation_reason_code,
        applicable_dimensions=applicable_dimensions or [],
        dimension_evidence=evidence,
        rationale="根据候选人回答中的直接证据进行评估。",
        critique="需要补充更具体的取舍依据。",
        better_answer="补充背景、动作、取舍、风险和度量。",
        references=[],
    )


def report_with(feedbacks: list[InterviewFeedback]) -> InterviewReport:
    return InterviewReport(
        session_id="session-1",
        overall_score=80,
        overall_dimension_scores=DimensionScores(
            breadth=80,
            depth=80,
            architecture=80,
            engineering=80,
            communication=80,
        ),
        summary="本轮回答已经完成确定性评分。",
        highlights=["评分只使用候选人回答证据"],
        feedbacks=feedbacks,
    )


def test_all_answered_with_sufficient_evidence_is_scored_and_complete():
    items = [
        feedback(
            "q1",
            score=81,
            dimensions=DimensionScores(depth=80, engineering=82),
            applicable_dimensions=["depth", "engineering"],
            observed={"depth": ["解释了并发窗口"], "engineering": ["给出回退步骤"]},
        ),
        feedback(
            "q2",
            score=89,
            dimensions=DimensionScores(depth=90, engineering=88),
            applicable_dimensions=["depth", "engineering"],
            observed={"depth": ["说明失败模式"], "engineering": ["说明监控指标"]},
        ),
    ]

    coverage = aggregate_report_coverage(items)

    assert coverage.score_status == "scored"
    assert coverage.coverage_status == "complete"
    assert coverage.overall_score == 85
    assert coverage.overall_dimension_scores.depth == 85
    assert coverage.dimensions["depth"].evaluated_count == 2
    assert coverage.dimensions["depth"].eligible_count == 2
    assert coverage.dimensions["depth"].evidence_count == 2


def test_partial_only_publishes_evaluated_items_and_keeps_denominator():
    items = [
        feedback(
            "q1",
            score=84,
            dimensions=DimensionScores(depth=84),
            applicable_dimensions=["depth"],
            observed={"depth": ["解释了具体机制"]},
        ),
        feedback(
            "q2",
            score=None,
            dimensions=DimensionScores(),
            evaluation_status="insufficient_evidence",
            evaluation_reason_code="evidence_extraction_failed",
            applicable_dimensions=["depth", "engineering"],
        ),
        feedback(
            "q3",
            answer_state="skipped",
            score=None,
            dimensions=DimensionScores(),
            evaluation_status="not_evaluated",
            evaluation_reason_code="skipped",
        ),
    ]

    coverage = aggregate_report_coverage(items)

    assert coverage.score_status == "partial"
    assert coverage.coverage_status == "partial"
    assert coverage.overall_score == 84
    assert coverage.evaluated_count == 1
    assert coverage.total_eligible_count == 2
    assert coverage.dimensions["engineering"].status == "insufficient_evidence"
    assert coverage.dimensions["engineering"].score is None


def test_all_skipped_is_unscored_and_never_fabricates_zero():
    items = [
        feedback(
            "q1",
            answer_state="skipped",
            score=None,
            dimensions=DimensionScores(),
            evaluation_status="not_evaluated",
            evaluation_reason_code="skipped",
        )
    ]

    coverage = aggregate_report_coverage(items)

    assert coverage.score_status == "unscored"
    assert coverage.score_reason_code == "no_answered_questions"
    assert coverage.overall_score is None
    assert all(
        value is None
        for value in coverage.overall_dimension_scores.model_dump().values()
    )


def test_degraded_wording_does_not_erase_valid_rule_scores():
    base = report_with(
        [
            feedback(
                "q1",
                score=76,
                dimensions=DimensionScores(depth=76),
                applicable_dimensions=["depth"],
                observed={"depth": ["说明了数据库更新后的缓存删除"]},
            )
        ]
    ).model_copy(
        update={
            "generation_status": "degraded",
            "generation_reason_code": "summary_provider_failed",
        }
    )

    result = apply_report_coverage(base)

    assert result.generation_status == "degraded"
    assert result.score_status == "scored"
    assert result.overall_score == 76


def test_evidence_extraction_failure_is_unscored_with_per_axis_reasons():
    item = feedback(
        "q1",
        score=None,
        dimensions=DimensionScores(),
        evaluation_status="insufficient_evidence",
        evaluation_reason_code="evidence_extraction_failed",
        applicable_dimensions=["depth", "engineering"],
    )
    base = report_with([item]).model_copy(
        update={
            "overall_score": None,
            "overall_dimension_scores": DimensionScores(),
            "score_status": "unscored",
            "coverage_status": "none",
        }
    )

    result = apply_report_coverage(base)

    assert result.overall_score is None
    assert result.question_evaluations["q1"].status == "insufficient_evidence"
    assert result.question_evaluations["q1"].reason_code == "evidence_extraction_failed"
    assert result.feedbacks[0].dimension_evaluations["depth"].score is None
    assert result.feedbacks[0].dimension_evaluations["architecture"].reason_code == "not_applicable"


def test_non_applicable_question_and_input_order_do_not_change_dimension_score():
    depth = feedback(
        "q1",
        score=73,
        dimensions=DimensionScores(depth=73),
        applicable_dimensions=["depth"],
        observed={"depth": ["解释了机制"]},
    )
    communication = feedback(
        "q2",
        score=95,
        dimensions=DimensionScores(communication=95),
        applicable_dimensions=["communication"],
        observed={"communication": ["表达结构清晰"]},
    )

    first = aggregate_report_coverage([depth, communication])
    second = aggregate_report_coverage([communication, depth])

    assert first.overall_dimension_scores.depth == 73
    assert second.overall_dimension_scores.depth == 73
    assert first.overall_dimension_scores == second.overall_dimension_scores
    assert "architecture" not in first.weakest_dimensions


def test_rounding_is_decimal_half_up_and_empty_input_is_unscored():
    first = feedback(
        "q1",
        score=80,
        dimensions=DimensionScores(depth=80),
        applicable_dimensions=["depth"],
    )
    second = feedback(
        "q2",
        score=81,
        dimensions=DimensionScores(depth=81),
        applicable_dimensions=["depth"],
    )

    assert aggregate_report_coverage([first, second]).overall_score == 81
    empty = aggregate_report_coverage([])
    assert empty.score_status == "unscored"
    assert empty.overall_score is None
    assert empty.strongest_dimensions == []
    assert empty.weakest_dimensions == []


def test_fixed_fallback_score_and_quality_bypass_cannot_regress():
    repository = Path(__file__).resolve().parents[1]
    evaluator_source = (repository / "app/services/evaluator.py").read_text(
        encoding="utf-8"
    )
    runtime_quality_source = (
        repository / "app/services/report_runtime_quality.py"
    ).read_text(encoding="utf-8")
    react_source = (
        repository / "frontend/src/pages/ReportDetailPage.jsx"
    ).read_text(encoding="utf-8")

    assert "overall_score=60" not in evaluator_source
    assert "_default_dimension_scores" not in evaluator_source
    assert "if report.is_fallback" not in runtime_quality_source
    assert "分数和反馈仍来自真实会话" not in react_source
