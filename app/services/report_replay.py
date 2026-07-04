import json
from pathlib import Path

from app.services.report import InterviewReport
from app.services.report_contract import assemble_interview_report
from app.services.report_provider_adapter import normalize_provider_payload


def replay_fixture(path: str) -> InterviewReport:
    fixture_path = Path(path)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    provider_payload = fixture["provider_payload"]
    evaluation_items = fixture["evaluation_items"]
    normalized = normalize_provider_payload(
        provider_payload,
        evaluation_items,
    )

    session_id = str(provider_payload.get("session_id") or fixture_path.stem)
    return assemble_interview_report(
        session_id=session_id,
        question_results=normalized.question_results,
        reference_lookup=normalized.reference_lookup,
    )
