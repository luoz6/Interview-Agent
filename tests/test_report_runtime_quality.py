from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.report_runtime_quality import (
    RuntimeReportQualityResult,
    evaluate_runtime_report_quality,
)


def make_feedback(
    *,
    answer_state: str = "answered",
    score: int = 82,
    rationale: str = "\u56de\u7b54\u8bf4\u660e\u4e86\u7f13\u5b58\u5931\u6548\u4e3b\u8def\u5f84\uff0c\u5e76\u8865\u5145\u4e86\u7a97\u53e3\u7ade\u4e89\u548c\u56de\u9000\u7b56\u7565\u3002",
    critique: str = "\u8fd8\u53ef\u4ee5\u8865\u5145\u76d1\u63a7\u95ed\u73af\u548c\u91cf\u5316\u6536\u76ca\u3002",
    better_answer: str = "\u5efa\u8bae\u8865\u5145\u53cc\u5220\u3001\u56de\u9000\u8bfb\u53d6\u3001\u76d1\u63a7\u6307\u6807\u548c\u98ce\u9669\u7f13\u89e3\u3002",
    user_answer: str = "\u6211\u4f1a\u5728\u6570\u636e\u5e93\u63d0\u4ea4\u540e\u5220\u9664\u7f13\u5b58\uff0c\u5e76\u8865\u5145\u56de\u9000\u8bfb\u53d6\u548c p95 \u76d1\u63a7\u3002",
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis cache invalidation.",
        user_answer=user_answer,
        answer_state=answer_state,
        score=score,
        dimension_scores=DimensionScores(
            breadth=score,
            depth=score,
            architecture=score,
            engineering=score,
            communication=score,
        ),
        rationale=rationale,
        critique=critique,
        better_answer=better_answer,
        references=[],
    )


def make_report(
    *,
    summary: str,
    feedbacks: list[InterviewFeedback],
    is_fallback: bool = False,
) -> InterviewReport:
    score = feedbacks[0].score if feedbacks else 0
    return InterviewReport(
        session_id="s1",
        overall_score=score,
        overall_dimension_scores=DimensionScores(
            breadth=score,
            depth=score,
            architecture=score,
            engineering=score,
            communication=score,
        ),
        summary=summary,
        highlights=["\u56de\u7b54\u8986\u76d6\u4e86\u4e3b\u6d41\u7a0b\u3002"],
        feedbacks=feedbacks,
        is_fallback=is_fallback,
    )


def test_runtime_report_quality_allows_clean_grounded_report():
    result = evaluate_runtime_report_quality(
        make_report(
            summary="\u56de\u7b54\u4e3b\u7ebf\u5b8c\u6574\uff0c\u5e76\u89e3\u91ca\u4e86\u56de\u9000\u7b56\u7565\u4e0e\u4e00\u81f4\u6027\u53d6\u820d\u3002",
            feedbacks=[make_feedback()],
        ),
        expected_question_count=1,
    )

    assert result == RuntimeReportQualityResult(blocking_issues=[], warning_issues=[])


def test_runtime_report_quality_blocks_grounded_report_with_stage27_issues():
    result = evaluate_runtime_report_quality(
        make_report(
            summary="Strong answer with room for stronger metrics.",
            feedbacks=[
                make_feedback(
                    rationale="Good answer.",
                    critique="Needs more details.",
                    better_answer="Add more details.",
                )
            ],
        ),
        expected_question_count=1,
    )

    assert "summary must include Simplified Chinese text" in result.blocking_issues
    assert "feedback[q1].rationale must not be placeholder text" in result.blocking_issues
    assert result.warning_issues == []


def test_runtime_report_quality_does_not_block_fallback_report():
    result = evaluate_runtime_report_quality(
        make_report(
            summary="Evidence was insufficient for a grounded expert report.",
            feedbacks=[
                make_feedback(
                    rationale="Fallback report generated because grounded evidence was insufficient.",
                    critique="Needs sharper metrics.",
                    better_answer="I reduced p95 latency with Redis and fallback.",
                )
            ],
            is_fallback=True,
        ),
        expected_question_count=1,
    )

    assert result.blocking_issues == []
    assert "fallback report bypassed runtime quality enforcement" in result.warning_issues
