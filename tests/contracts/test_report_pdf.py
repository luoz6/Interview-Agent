from io import BytesIO

import pdfplumber
from pypdf import PdfReader

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportClaimV2,
    ReportEvidenceRefV2,
    ReportLimitationV2,
    ReportPriorityActionV2,
    ReportTechnicalAppendixV2,
)
from app.services.report_pdf import (
    _build_story,
    _build_styles,
    _dimension_table,
    _feedback_story,
    _register_pdf_fonts,
    build_report_pdf,
    ReportPdfRenderer,
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
        strengths=[
            ReportClaimV2(
                claim_id="strength-1",
                kind="strength",
                text="Explained cache tradeoffs.",
                evidence_refs=["candidate:q1:answer"],
            )
        ],
        priority_actions=[
            ReportPriorityActionV2(
                action_id="action-1",
                title="Add measurable outcomes",
                why_it_matters="The answer lacks a verifiable result.",
                practice="Rewrite the final sentence using an existing metric.",
                completion_criteria="The result includes a sourced metric and window.",
                question_refs=["q1"],
                observation_refs=["obs-0000000000000001"],
                evidence_refs=["candidate:q1:answer"],
            )
        ],
        limitations=[
            ReportLimitationV2(
                limitation_id="limitation-1",
                text="Only one project answer was evaluated.",
                reason_code="limited_sample",
            )
        ],
        evidence_refs=[
            ReportEvidenceRefV2(
                evidence_ref_id="candidate:q1:answer",
                namespace="candidate",
                question_id="q1",
                excerpt="The candidate built a Redis-backed API.",
            )
        ],
        technical_appendix=ReportTechnicalAppendixV2(
            reason_codes=["limited_sample"],
            report_path="full_session",
            summary_generation_mode="deterministic",
        ),
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
    pdf_bytes = ReportPdfRenderer().render(make_report())

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_build_report_pdf_supports_fallback_reports():
    pdf_bytes = ReportPdfRenderer().render(make_report(is_fallback=True))

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

    assert "知识引用 [id=redis_consistency]" in paragraph_text


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

    pdf = ReportPdfRenderer().render(report)

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

    assert "综合评分：未评分" in text
    assert "题目评分：证据不足" in text
    assert "综合评分：None" not in text
    assert all(row[1] == "未评估" for row in table._cellvalues[1:])


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

    assert "综合评分：81/100（部分评分 1/2）" in text


def test_pdf_uses_same_structured_summary_actions_evidence_and_limitations_as_web():
    report = make_report()

    story = _build_story(
        report,
        report_id="report-immutable-1",
        revision=7,
        created_at="2026-08-06T08:00:00Z",
    )
    paragraphs = [
        block.getPlainText() for block in story if hasattr(block, "getPlainText")
    ]
    text = "\n".join(paragraphs)

    assert report.summary in text
    assert report.priority_actions[0].title in text
    assert report.priority_actions[0].practice in text
    assert report.priority_actions[0].completion_criteria in text
    assert report.evidence_refs[0].evidence_ref_id in text
    assert report.evidence_refs[0].excerpt in text
    assert report.limitations[0].text in text
    assert "Report ID：report-immutable-1" in text
    assert "报告版本：第 7 版" in text
    assert paragraphs.index("技术附录") > paragraphs.index("06 评估限制")


def test_pdf_with_artifact_identity_reopens_and_has_versioned_metadata():
    pdf_bytes = build_report_pdf(
        make_report(),
        report_id="report-immutable-1",
        revision=7,
        created_at="2026-08-06T08:00:00Z",
    )

    reader = PdfReader(BytesIO(pdf_bytes))

    assert len(reader.pages) >= 2
    assert reader.metadata.title == "Interview Report R7 report-immutable-1"


def test_pdf_text_extraction_preserves_identity_semantics_and_appendix_order():
    pdf_bytes = build_report_pdf(
        make_report(),
        report_id="report-extract-1",
        revision=3,
        created_at="2026-08-06T08:00:00Z",
    )

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]

    text = "\n".join(page_texts)
    assert "Report ID：report-extract-1" in text
    assert "报告版本：第 3 版" in text
    assert make_report().priority_actions[0].title in text
    assert make_report().limitations[0].text in text
    assert "技术附录" in page_texts[-1]
    assert all("技术附录" not in page for page in page_texts[:-1])
