import json
from pathlib import Path

from app.services.report import InterviewReport
from app.services.report_contract import assemble_interview_report
from app.services.report_provider_adapter import normalize_provider_payload
from app.services.report_quality import collect_report_quality_issues


def replay_fixture(path: str) -> InterviewReport:
    report, _ = replay_fixture_with_quality(path)
    return report


def replay_fixture_with_quality(path: str) -> tuple[InterviewReport, list[str]]:
    fixture_path = Path(path)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    provider_payload = fixture["provider_payload"]
    evaluation_items = fixture["evaluation_items"]
    normalized = normalize_provider_payload(
        provider_payload,
        evaluation_items,
    )

    session_id = str(provider_payload.get("session_id") or fixture_path.stem)
    report = assemble_interview_report(
        session_id=session_id,
        question_results=normalized.question_results,
        reference_lookup=normalized.reference_lookup,
    )
    issues = collect_report_quality_issues(
        report,
        expected_question_count=len(normalized.question_results),
    )
    return report, issues
