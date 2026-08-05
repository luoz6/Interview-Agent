from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)
from app.services.report_pdf import (
    _build_story,
    _build_styles,
    _dimension_table,
    _feedback_story,
    _register_pdf_fonts,
    build_report_pdf,
)


def make_dimension_scores(score: int = 81) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_report(*, is_fallback: bool = False) -> InterviewReport:
    return InterviewReport(
        session_id="session-pdf-1",
        overall_score=81,
        overall_dimension_scores=make_dimension_scores(81),
        summary="Clear project story with practical tradeoffs.",
        highlights=["Explained cache tradeoffs.", "Used fallback strategy."],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Introduce a backend project.",
                user_answer="The candidate built a Redis-backed API.",
                score=81,
                dimension_scores=make_dimension_scores(81),
                rationale="The answer linked design choices to the workload.",
                critique="Business outcome metrics were weak.",
                better_answer="I reduced p95 latency with cache-aside Redis.",
                references=[],
            )
        ],
        is_fallback=is_fallback,
    )


def test_build_report_pdf_returns_pdf_bytes():
    pdf_bytes = build_report_pdf(make_report())

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_build_report_pdf_supports_fallback_reports():
    pdf_bytes = build_report_pdf(make_report(is_fallback=True))

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_report_pdf_reference_includes_evidence_id():
    _register_pdf_fonts()
    feedback = make_report().feedbacks[0].model_copy(
        update={
            "references": [
                FeedbackReference(
                    chunk_id="redis_consistency",
                    title="Redis consistency",
                    source_type="theory",
                    excerpt="Cache-aside consistency evidence.",
                )
            ]
        }
    )

    blocks = _feedback_story(feedback, _build_styles())
    paragraph_text = "\n".join(
        block.getPlainText() for block in blocks if hasattr(block, "getPlainText")
    )

    assert "[id=redis_consistency]" in paragraph_text


def test_report_pdf_contains_skipped_answer_marker():
    report = make_report()
    skipped_feedback = report.feedbacks[0].model_copy(
        update={
            "user_answer": "Question was skipped by the candidate.",
            "answer_state": "skipped",
            "score": None,
            "dimension_scores": DimensionScores(),
            "evaluation_status": "not_evaluated",
            "evaluation_reason_code": "skipped",
        }
    )
    report = report.model_copy(update={"feedbacks": [skipped_feedback]})

    pdf = build_report_pdf(report)

    assert b"%PDF" in pdf[:20]
    assert len(pdf) > 1000


def test_dimension_table_uses_chinese_labels():
    table = _dimension_table(make_report())

    assert table._cellvalues[0] == ["维度", "分数"]
    assert table._cellvalues[1][0] == "知识广度"
    assert table._cellvalues[2][0] == "技术深度"
    assert table._cellvalues[3][0] == "系统设计"
    assert table._cellvalues[4][0] == "工程实践"
    assert table._cellvalues[5][0] == "表达沟通"


def test_unscored_pdf_never_prints_none_or_numeric_placeholders():
    report = make_report().model_copy(
        update={
            "overall_score": None,
            "overall_dimension_scores": DimensionScores(),
            "score_status": "unscored",
            "score_reason_code": "insufficient_evidence",
            "coverage_status": "none",
            "feedbacks": [
                make_report().feedbacks[0].model_copy(
                    update={
                        "score": None,
                        "dimension_scores": DimensionScores(),
                        "evaluation_status": "insufficient_evidence",
                        "evaluation_reason_code": "evidence_extraction_failed",
                    }
                )
            ],
        }
    )

    story = _build_story(report)
    text = "\n".join(
        block.getPlainText() for block in story if hasattr(block, "getPlainText")
    )
    table = _dimension_table(report)

    assert "Overall Score: Not scored" in text
    assert "Score: Insufficient evidence" in text
    assert "Overall Score: None" not in text
    assert all(row[1] == "Not evaluated" for row in table._cellvalues[1:])


def test_partial_pdf_prints_coverage_denominator():
    report = make_report().model_copy(
        update={
            "score_status": "partial",
            "coverage_status": "partial",
            "evaluated_count": 1,
            "total_eligible_count": 2,
        }
    )

    story = _build_story(report)
    text = "\n".join(
        block.getPlainText() for block in story if hasattr(block, "getPlainText")
    )

    assert "Overall Score: 81/100 (partial 1/2)" in text
