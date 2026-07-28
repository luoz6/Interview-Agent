from dataclasses import dataclass
import re

from app.services.report import InterviewReport
from app.services.report_quality import collect_report_quality_issues


@dataclass(frozen=True)
class RuntimeReportQualityResult:
    blocking_issues: list[str]
    warning_issues: list[str]

    @property
    def structured_blocking_issues(self) -> list["QualityIssue"]:
        return [quality_issue_from_description(item) for item in self.blocking_issues]


@dataclass(frozen=True)
class QualityIssue:
    code: str
    description: str
    question_id: str | None = None


def quality_issue_from_description(description: str) -> QualityIssue:
    question_match = re.match(r"feedback\[([^]]+)\]", description)
    question_id = question_match.group(1) if question_match else None
    if description.startswith("feedback count mismatch"):
        code = "feedback_count_mismatch"
    elif description.startswith("summary must include"):
        code = "summary_no_chinese"
    elif "overall_score" in description or "overall_dimension_scores" in description:
        code = "score_mismatch"
    elif "dimension_evidence must not be empty" in description:
        code = "dimension_evidence_empty"
    elif ".references" in description:
        code = "invalid_feedback_reference"
    elif "must include Simplified Chinese" in description:
        code = "feedback_no_chinese"
    elif "placeholder" in description:
        code = "placeholder_feedback"
    elif "must not be blank" in description:
        code = "blank_feedback"
    else:
        code = "report_quality_violation"
    return QualityIssue(code=code, description=description, question_id=question_id)


def evaluate_runtime_report_quality(
    report: InterviewReport,
    *,
    expected_question_count: int,
) -> RuntimeReportQualityResult:
    if report.is_fallback:
        return RuntimeReportQualityResult(
            blocking_issues=[],
            warning_issues=["fallback report bypassed runtime quality enforcement"],
        )

    return RuntimeReportQualityResult(
        blocking_issues=collect_report_quality_issues(
            report,
            expected_question_count=expected_question_count,
        ),
        warning_issues=[],
    )
