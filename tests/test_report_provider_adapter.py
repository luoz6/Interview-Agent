import json
from pathlib import Path

import pytest

from app.services.report import InterviewReport
from app.services.report_contract import CanonicalQuestionResult
from app.services.report_provider_adapter import (
    build_reference_lookup,
    normalize_provider_payload,
)
from app.services.report_replay import replay_fixture


FIXTURE_DIR = Path("tests/fixtures/report_payloads")


@pytest.mark.parametrize(
    "fixture_name",
    [
        "deepseek_adjacent.json",
        "deepseek_sparse.json",
        "deepseek_evaluation_results.json",
    ],
)
def test_normalize_provider_payload_converts_known_deepseek_shapes(fixture_name: str):
    fixture = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
    result = normalize_provider_payload(
        fixture["provider_payload"],
        fixture["evaluation_items"],
    )

    assert len(result.question_results) == 1
    assert isinstance(result.question_results[0], CanonicalQuestionResult)
    assert result.question_results[0].question_id == "q1"
    assert result.question_results[0].reference_chunk_ids
    assert "redis-1" in build_reference_lookup(
        fixture["provider_payload"],
        fixture["evaluation_items"],
        result.provider_reference_ids,
    )


def test_replay_fixture_returns_grounded_report_for_deepseek_adjacent():
    report = replay_fixture(str(FIXTURE_DIR / "deepseek_adjacent.json"))

    assert isinstance(report, InterviewReport)
    assert report.session_id == "s1"
    assert report.is_fallback is False
    assert report.overall_score == 48
    assert report.feedbacks[0].user_answer == "I delete cache after database writes."
    assert report.feedbacks[0].dimension_scores.depth == 55
    assert report.feedbacks[0].dimension_scores.engineering == 55
    assert report.feedbacks[0].applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]
    assert report.feedbacks[0].dimension_evidence
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1",
        "redis-2",
    ]
    assert report.feedbacks[0].references[1].source_type == "answer"
    assert "延迟双删" in report.feedbacks[0].references[1].excerpt
