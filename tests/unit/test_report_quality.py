from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.report_quality import collect_report_quality_issues


def make_feedback(
    *,
    answer_state: str = "answered",
    score: int = 82,
    rationale: str = "回答说明了缓存删除时机，但还缺少一致性窗口分析。",
    critique: str = "缺少并发竞争和回退路径说明。",
    better_answer: str = "补充双删、回退读取和监控指标。",
    user_answer: str = "我会在数据库提交后删除缓存，并观察 p95 延迟。",
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
        applicable_dimensions=[
            "breadth",
            "depth",
            "architecture",
            "engineering",
            "communication",
        ],
        dimension_evidence=[
            {
                "dimension": "depth",
                "observed": ["候选人说明了缓存删除时机。"],
                "missing": ["还缺少一致性窗口分析。"],
                "quality_signals": ["concept", "concrete_steps"],
            }
        ],
        rationale=rationale,
        critique=critique,
        better_answer=better_answer,
        references=[],
    )


def make_report(*, summary: str, feedbacks: list[InterviewFeedback]) -> InterviewReport:
    first_score = feedbacks[0].score if feedbacks else 0
    return InterviewReport(
        session_id="s1",
        overall_score=first_score,
        overall_dimension_scores=DimensionScores(
            breadth=first_score,
            depth=first_score,
            architecture=first_score,
            engineering=first_score,
            communication=first_score,
        ),
        summary=summary,
        highlights=["回答覆盖了主流程。"],
        feedbacks=feedbacks,
    )


def test_report_quality_rejects_english_summary_and_placeholder_feedback():
    report = make_report(
        summary="Solid answer with room for stronger metrics.",
        feedbacks=[
            make_feedback(
                rationale="Good answer.",
                critique="Needs more details.",
                better_answer="Add more details.",
            )
        ],
    )

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "summary must include Simplified Chinese text" in issues
    assert "feedback[q1].rationale must include Simplified Chinese text" in issues
    assert "feedback[q1].rationale must not be placeholder text" in issues
    assert "feedback[q1].critique must not be placeholder text" in issues
    assert "feedback[q1].better_answer must not be placeholder text" in issues


def test_report_quality_rejects_nonzero_score_for_non_answered_feedback():
    report = make_report(
        summary="这是一次需要补强的回答。",
        feedbacks=[
            make_feedback(
                answer_state="skipped",
                score=52,
                user_answer="候选人跳过了这道题。",
            )
        ],
    )

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "feedback[q1].score must be 0 when answer_state is skipped" in issues


def test_report_quality_accepts_valid_chinese_report():
    report = make_report(
        summary="回答主线完整，但还需要补充并发一致性与回退策略。",
        feedbacks=[make_feedback()],
    )

    assert collect_report_quality_issues(report, expected_question_count=1) == []


def test_report_quality_rejects_answered_feedback_without_rule_evidence():
    report = make_report(
        summary="回答主线完整，但还需要补充风险和指标。",
        feedbacks=[
            make_feedback(
                score=82,
                rationale="回答说明了缓存主路径。",
                critique="缺少并发窗口。",
                better_answer="补充延迟双删和降级读取。",
            )
        ],
    )
    report.feedbacks[0].applicable_dimensions = ["depth", "engineering"]
    report.feedbacks[0].dimension_evidence = []

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "feedback[q1].dimension_evidence must not be empty for answered questions" in issues


def test_report_quality_rejects_report_aggregate_mismatch():
    feedback = make_feedback(score=60)
    feedback.applicable_dimensions = ["depth", "engineering", "communication"]
    feedback.dimension_evidence = [
        {
            "dimension": "depth",
            "observed": ["候选人说明了缓存删除顺序。"],
            "missing": ["缺少失败补偿。"],
            "quality_signals": ["concrete_steps"],
        }
    ]
    report = make_report(
        summary="回答覆盖了部分技术路径。",
        feedbacks=[feedback],
    )
    report.overall_score = 99

    issues = collect_report_quality_issues(report, expected_question_count=1)

    assert "overall_score must equal backend aggregate score" in issues
