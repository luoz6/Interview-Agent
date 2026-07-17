from dataclasses import dataclass

from app.services.report import InterviewReport
from app.services.report_quality import collect_report_quality_issues


@dataclass(frozen=True)
class RuntimeReportQualityResult:
    blocking_issues: list[str]
    warning_issues: list[str]


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
