from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.report import InterviewFeedback, InterviewReport


_FONT_NAME = "STSong-Light"
_DIMENSION_LABELS = {
    "breadth": "知识广度",
    "depth": "技术深度",
    "architecture": "系统设计",
    "engineering": "工程实践",
    "communication": "表达沟通",
}


class ReportPdfRenderer:
    def render(
        self,
        report: InterviewReport,
        *,
        report_id: str | None = None,
        revision: int | None = None,
        created_at: str | None = None,
    ) -> bytes:
        _register_pdf_fonts()
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=(
                f"Interview Report R{revision} {report_id}"
                if report_id and revision is not None
                else f"Interview Report {report.session_id}"
            ),
            author="Interview Agent",
        )
        document.build(
            _build_story(
                report,
                report_id=report_id,
                revision=revision,
                created_at=created_at,
            )
        )
        return buffer.getvalue()


def build_report_pdf(
    report: InterviewReport,
    *,
    report_id: str | None = None,
    revision: int | None = None,
    created_at: str | None = None,
) -> bytes:
    """Render one immutable report payload with optional Artifact identity."""
    return ReportPdfRenderer().render(
        report,
        report_id=report_id,
        revision=revision,
        created_at=created_at,
    )


def _register_pdf_fonts() -> None:
    registered = pdfmetrics.getRegisteredFontNames()
    if _FONT_NAME not in registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))


def _build_story(
    report: InterviewReport,
    *,
    report_id: str | None = None,
    revision: int | None = None,
    created_at: str | None = None,
) -> list[Any]:
    styles = _build_styles()
    story: list[Any] = [
        Paragraph("结构化面评报告", styles["title"]),
        Spacer(1, 6),
        Paragraph(f"会话：{_safe(report.session_id)}", styles["meta"]),
    ]
    if report_id is not None:
        story.append(Paragraph(f"Report ID：{_safe(report_id)}", styles["meta"]))
    if revision is not None:
        story.append(Paragraph(f"报告版本：第 {revision} 版", styles["meta"]))
    if created_at is not None:
        story.append(Paragraph(f"生成时间：{_safe(created_at)}", styles["meta"]))
    story.extend(
        [
            Spacer(1, 8),
            Paragraph(f"综合评分：{_overall_score_label(report)}", styles["score"]),
            Paragraph(
                "；".join(
                    [
                        f"评分状态：{_score_status_label(report.score_status)}",
                        f"覆盖状态：{_coverage_status_label(report.coverage_status)}",
                        f"生成状态：{_generation_status_label(report.generation_status)}",
                    ]
                ),
                styles["meta"],
            ),
            Spacer(1, 10),
            Paragraph("01 本轮结论与评分状态", styles["section"]),
            Paragraph(_safe(report.summary), styles["body"]),
            Spacer(1, 10),
            Paragraph("02 覆盖度和限制", styles["section"]),
            Paragraph(_coverage_summary(report), styles["body"]),
            Spacer(1, 6),
            _dimension_table(report),
            Spacer(1, 10),
            Paragraph("03 主要优势", styles["section"]),
        ]
    )
    strengths = report.strengths or [
        {"text": text, "evidence_refs": []} for text in report.highlights
    ]
    for strength in strengths[:3]:
        text = strength.text if hasattr(strength, "text") else strength["text"]
        evidence_refs = (
            strength.evidence_refs
            if hasattr(strength, "evidence_refs")
            else strength["evidence_refs"]
        )
        suffix = f"（证据：{_safe(', '.join(evidence_refs))}）" if evidence_refs else ""
        story.append(Paragraph(f"- {_safe(text)}{suffix}", styles["body"]))

    story.extend([Spacer(1, 10), Paragraph("04 Top 1-3 改进动作", styles["section"])])
    if report.priority_actions:
        for index, action in enumerate(report.priority_actions, 1):
            story.extend(
                [
                    Paragraph(f"{index}. {_safe(action.title)}", styles["action_title"]),
                    Paragraph(f"为什么重要：{_safe(action.why_it_matters)}", styles["body"]),
                    Paragraph(f"怎么练：{_safe(action.practice)}", styles["body"]),
                    Paragraph(f"完成标准：{_safe(action.completion_criteria)}", styles["body"]),
                    Paragraph(
                        "证据绑定："
                        f"题目 [{_safe(', '.join(action.question_refs) or '未提供')}]；"
                        f"证据 [{_safe(', '.join(action.evidence_refs))}]",
                        styles["meta"],
                    ),
                    Spacer(1, 6),
                ]
            )
    else:
        story.append(Paragraph("当前报告没有足够证据生成可靠的改进动作。", styles["body"]))

    story.extend(
        [
            Spacer(1, 10),
            Paragraph("05 逐题证据与回答建议", styles["section"]),
        ]
    )
    for feedback in report.feedbacks:
        story.extend(_feedback_story(feedback, styles))

    if report.evidence_refs:
        story.extend([Spacer(1, 8), Paragraph("报告证据清单", styles["subsection"])])
        for evidence in report.evidence_refs:
            identity = evidence.question_id or evidence.source_id or "未提供来源"
            story.append(
                Paragraph(
                    f"[{_safe(evidence.evidence_ref_id)}] "
                    f"{_safe(evidence.namespace)} / {_safe(identity)}："
                    f"{_safe(evidence.excerpt)}",
                    styles["body"],
                )
            )

    story.extend([Spacer(1, 10), Paragraph("06 评估限制", styles["section"])])
    if report.limitations:
        for index, limitation in enumerate(report.limitations, 1):
            story.append(Paragraph(f"{index}. {_safe(limitation.text)}", styles["body"]))
    else:
        story.append(
            Paragraph(
                "除本轮题目和回答的样本范围外，没有记录额外评估限制。",
                styles["body"],
            )
        )

    story.extend(
        [
            PageBreak(),
            Paragraph("技术附录", styles["section"]),
            Paragraph("以下内容用于版本和运行诊断，不改变候选人一级结论。", styles["meta"]),
            Spacer(1, 6),
            Paragraph(
                f"Report ID：{_safe(report_id or 'legacy')}；"
                f"Revision：{revision if revision is not None else 'legacy'}；"
                f"Schema：{_safe(report.report_schema_version)}；"
                f"Rubric：{_safe(report.scoring_rubric_version)}",
                styles["body"],
            ),
            Paragraph(
                f"报告路径：{_safe(report.report_path)}；"
                f"生成原因：{_safe(report.generation_reason_code)}；"
                f"评分原因：{_safe(report.score_reason_code)}",
                styles["body"],
            ),
            Paragraph(
                "Reason codes："
                + _safe(
                    ", ".join(
                        dict.fromkeys(
                            [
                                report.generation_reason_code,
                                report.score_reason_code,
                                *report.technical_appendix.reason_codes,
                                *[item.reason_code for item in report.limitations],
                            ]
                        )
                    )
                ),
                styles["body"],
            ),
            Paragraph(
                "Summary mode："
                f"{_safe(report.technical_appendix.summary_generation_mode or '未记录')}",
                styles["body"],
            ),
        ]
    )
    return story


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName=_FONT_NAME),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName=_FONT_NAME),
        "subsection": ParagraphStyle(
            "subsection",
            parent=base["Heading3"],
            fontName=_FONT_NAME,
        ),
        "action_title": ParagraphStyle(
            "action_title",
            parent=base["Heading3"],
            fontName=_FONT_NAME,
            textColor=colors.HexColor("#1E4E8C"),
        ),
        "meta": ParagraphStyle("meta", parent=base["BodyText"], fontName=_FONT_NAME),
        "score": ParagraphStyle("score", parent=base["Heading1"], fontName=_FONT_NAME),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            leading=16,
            spaceAfter=3,
        ),
        "warning": ParagraphStyle(
            "warning",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            textColor=colors.darkorange,
        ),
    }


def _dimension_table(report: InterviewReport) -> Table:
    rows = [["维度", "分数"]]
    for name, value in report.overall_dimension_scores.model_dump().items():
        evaluation = report.dimension_evaluations.get(name)
        rows.append(
            [
                _DIMENSION_LABELS.get(name, name),
                _score_value_label(
                    value,
                    status=evaluation.status if evaluation is not None else None,
                ),
            ]
        )
    table = Table(rows, colWidths=[70 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7EEF7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB7C4")),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _feedback_story(
    feedback: InterviewFeedback,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    blocks: list[Any] = [
        Spacer(1, 8),
        Paragraph(_safe(feedback.question_text), styles["subsection"]),
        Paragraph(
            f"题目评分：{_score_value_label(feedback.score, status=feedback.evaluation_status)}",
            styles["body"],
        ),
    ]
    status_label = _answer_status_label(feedback)
    if status_label:
        blocks.append(Paragraph(status_label, styles["warning"]))
    blocks.extend(
        [
            Paragraph(f"候选人回答：{_safe(feedback.user_answer)}", styles["body"]),
            Paragraph(f"评分依据：{_safe(feedback.rationale)}", styles["body"]),
            Paragraph(f"主要不足：{_safe(feedback.critique)}", styles["body"]),
            Paragraph(f"回答建议：{_safe(feedback.better_answer)}", styles["body"]),
        ]
    )
    for reference in feedback.references:
        blocks.append(
            Paragraph(
                f"知识引用 [id={_safe(reference.chunk_id)}]："
                f"{_safe(reference.title)} ({_safe(reference.source_type)}) - "
                f"{_safe(reference.excerpt)}",
                styles["body"],
            )
        )
    return blocks


def _answer_status_label(feedback: InterviewFeedback) -> str | None:
    if feedback.answer_state == "skipped":
        return "回答状态：已跳过"
    if feedback.answer_state == "unanswered":
        return "回答状态：未回答"
    if feedback.evaluation_status == "insufficient_evidence":
        return f"回答状态：证据不足（{_safe(feedback.evaluation_reason_code)}）"
    return None


def _overall_score_label(report: InterviewReport) -> str:
    if report.overall_score is None:
        return "未评分"
    if report.score_status == "partial":
        numerator = report.evaluated_count if report.evaluated_count is not None else "?"
        denominator = (
            report.total_eligible_count
            if report.total_eligible_count is not None
            else "?"
        )
        return f"{report.overall_score}/100（部分评分 {numerator}/{denominator}）"
    return f"{report.overall_score}/100"


def _score_value_label(value: int | None, *, status: str | None = None) -> str:
    if value is not None:
        return f"{value}/100"
    if status == "insufficient_evidence":
        return "证据不足"
    return "未评估"


def _coverage_summary(report: InterviewReport) -> str:
    numerator = report.evaluated_count if report.evaluated_count is not None else 0
    denominator = (
        report.total_eligible_count
        if report.total_eligible_count is not None
        else len(report.feedbacks)
    )
    evidence_count = report.evidence_count if report.evidence_count is not None else 0
    return (
        f"覆盖状态为 {_coverage_status_label(report.coverage_status)}；"
        f"{numerator}/{denominator} 道题进入评分；"
        f"报告包含 {evidence_count} 条结构化证据。"
    )


def _score_status_label(status: str) -> str:
    return {"scored": "已评分", "partial": "部分评分", "unscored": "未评分"}.get(
        status,
        status,
    )


def _coverage_status_label(status: str) -> str:
    return {"complete": "完整覆盖", "partial": "部分覆盖", "none": "无有效覆盖"}.get(
        status,
        status,
    )


def _generation_status_label(status: str) -> str:
    return {"complete": "完整生成", "degraded": "降级生成"}.get(status, status)


def _safe(value: Any) -> str:
    return escape(str(value), quote=False)
