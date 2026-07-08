from app.services.report import InterviewFeedback, InterviewReport


_PLACEHOLDER_TEXTS = {
    "good answer.",
    "needs more details.",
    "add more details.",
    "provider response did not include rationale.",
    "add explicit fallback, consistency, and mitigation details.",
}


def collect_report_quality_issues(
    report: InterviewReport,
    *,
    expected_question_count: int | None = None,
) -> list[str]:
    issues: list[str] = []
    if expected_question_count is not None and len(report.feedbacks) != expected_question_count:
        issues.append(
            f"feedback count mismatch: expected {expected_question_count}, got {len(report.feedbacks)}"
        )
    if not report.feedbacks:
        issues.append("report.feedbacks must not be empty")
        return issues
    if not _contains_chinese(report.summary):
        issues.append("summary must include Simplified Chinese text")
    for feedback in report.feedbacks:
        issues.extend(_feedback_quality_issues(feedback))
    return issues


def _feedback_quality_issues(feedback: InterviewFeedback) -> list[str]:
    issues: list[str] = []
    prefix = f"feedback[{feedback.question_id}]"

    for field_name in ("rationale", "critique", "better_answer"):
        value = getattr(feedback, field_name).strip()
        if not value:
            issues.append(f"{prefix}.{field_name} must not be blank")
            continue
        if not _contains_chinese(value):
            issues.append(f"{prefix}.{field_name} must include Simplified Chinese text")
        if _is_placeholder_text(value):
            issues.append(f"{prefix}.{field_name} must not be placeholder text")

    if feedback.answer_state != "answered" and feedback.score != 0:
        issues.append(
            f"{prefix}.score must be 0 when answer_state is {feedback.answer_state}"
        )
    if feedback.answer_state == "skipped" and "跳过" not in feedback.user_answer:
        issues.append(f"{prefix}.user_answer must explain that the question was skipped")
    if feedback.answer_state == "unanswered" and "未作答" not in feedback.user_answer:
        issues.append(f"{prefix}.user_answer must explain that the question was unanswered")
    return issues


def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _is_placeholder_text(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in _PLACEHOLDER_TEXTS
