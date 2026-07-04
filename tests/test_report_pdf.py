from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)
from app.services.report_pdf import _dimension_table, build_report_pdf


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


def test_dimension_table_uses_chinese_labels():
    table = _dimension_table(make_report())

    assert table._cellvalues[0] == ["维度", "分数"]
    assert table._cellvalues[1][0] == "知识广度"
    assert table._cellvalues[2][0] == "技术深度"
    assert table._cellvalues[3][0] == "系统设计"
    assert table._cellvalues[4][0] == "工程实践"
    assert table._cellvalues[5][0] == "表达沟通"
