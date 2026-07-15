from pathlib import Path

import pytest

from app.services.report_eval_case_builder import build_report_evaluation_input
from app.services.report_eval_dataset import load_evaluation_dataset


@pytest.fixture
def redis_case():
    dataset = load_evaluation_dataset(Path("tests/golden/report_quality_v1.json"))
    return next(case for case in dataset.cases if case.case_id == "redis-cache-consistency-strong")


def test_builder_returns_real_plan_and_provider_evaluation_item(redis_case):
    plan, items = build_report_evaluation_input(redis_case)

    assert plan.title == "Stage 40: redis-cache-consistency"
    assert plan.questions[0].id == redis_case.case_id
    assert plan.questions[0].kind == redis_case.question_kind
    assert items[0]["question_id"] == redis_case.case_id
    assert items[0]["question_kind"] == redis_case.question_kind
    assert items[0]["messages"] == [
        {
            "role": "candidate",
            "content": redis_case.answer,
            "question_id": redis_case.case_id,
        }
    ]
    assert items[0]["scoring_references"] == items[0]["answer_references"]
    assert items[0]["scoring_references"][0]["chunk_id"] == redis_case.reference.chunk_id
    assert items[0]["scoring_references"][0]["excerpt"] == redis_case.reference.content
    assert items[0]["applicable_dimensions"] == redis_case.expected_applicable_dimensions
