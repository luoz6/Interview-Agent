from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.report import InterviewFeedback, InterviewReport


_FONT_NAME = "STSong-Light"
_DIMENSION_LABELS = {
    "breadth": "知识广度",
    "depth": "技术深度",
    "architecture": "系统设计",
    "engineering": "工程实践",
    "communication": "表达沟通",
}


def build_report_pdf(report: InterviewReport) -> bytes:
    _register_pdf_fonts()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Interview Report {report.session_id}",
        author="Interview Agent",
    )
    document.build(_build_story(report))
    return buffer.getvalue()


def _register_pdf_fonts() -> None:
    registered = pdfmetrics.getRegisteredFontNames()
    if _FONT_NAME not in registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))


def _build_story(report: InterviewReport) -> list:
    styles = _build_styles()
    story = [
        Paragraph("Interview Report", styles["title"]),
        Spacer(1, 6),
        Paragraph(f"Session ID: {report.session_id}", styles["meta"]),
        Spacer(1, 8),
        Paragraph(f"Overall Score: {report.overall_score}", styles["score"]),
        Spacer(1, 8),
        Paragraph("Overall Summary", styles["section"]),
        Paragraph(report.summary, styles["body"]),
        Spacer(1, 8),
        Paragraph("Dimension Scores", styles["section"]),
        _dimension_table(report),
        Spacer(1, 8),
        Paragraph("Highlights", styles["section"]),
    ]
    for highlight in report.highlights:
        story.append(Paragraph(f"- {highlight}", styles["body"]))
    if report.is_fallback:
        story.extend(
            [
                Spacer(1, 8),
                Paragraph("Fallback report: evidence generation was degraded.", styles["warning"]),
            ]
        )
    story.extend([Spacer(1, 10), Paragraph("Question Feedback", styles["section"])])
    for feedback in report.feedbacks:
        story.extend(_feedback_story(feedback, styles))
    return story


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName=_FONT_NAME),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName=_FONT_NAME),
        "meta": ParagraphStyle("meta", parent=base["BodyText"], fontName=_FONT_NAME),
        "score": ParagraphStyle("score", parent=base["Heading1"], fontName=_FONT_NAME),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName=_FONT_NAME, leading=16),
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
        rows.append([_DIMENSION_LABELS.get(name, name), str(value)])
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


def _feedback_story(feedback: InterviewFeedback, styles: dict[str, ParagraphStyle]) -> list:
    blocks = [
        Spacer(1, 8),
        Paragraph(feedback.question_text, styles["section"]),
        Paragraph(f"Score: {feedback.score}", styles["body"]),
    ]
    status_label = _answer_status_label(feedback)
    if status_label:
        blocks.append(Paragraph(status_label, styles["warning"]))
    blocks.extend(
        [
            Paragraph(f"Answer: {feedback.user_answer}", styles["body"]),
            Paragraph(f"Rationale: {feedback.rationale}", styles["body"]),
            Paragraph(f"Critique: {feedback.critique}", styles["body"]),
            Paragraph(f"Better Answer: {feedback.better_answer}", styles["body"]),
        ]
    )
    for reference in feedback.references:
        blocks.append(
            Paragraph(
                f"Reference [id={reference.chunk_id}]: "
                f"{reference.title} ({reference.source_type}) - {reference.excerpt}",
                styles["body"],
            )
        )
    return blocks


def _answer_status_label(feedback: InterviewFeedback) -> str | None:
    if feedback.answer_state == "skipped":
        return "Status: skipped"
    if feedback.answer_state == "unanswered":
        return "Status: unanswered"
    return None
