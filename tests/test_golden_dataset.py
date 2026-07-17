import pytest

from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.report_quality import collect_report_quality_issues
from tests.eval_support import (
    GoldenLLM,
    GoldenVectorStore,
    contains_term,
    load_all_cases,
    make_state,
)


ALL_CASES = load_all_cases()


@pytest.mark.parametrize("case", ALL_CASES, ids=[case["id"] for case in ALL_CASES])
def test_golden_dataset_cases(case: dict):
    evaluator = ExpertShadowEvaluator(llm=GoldenLLM(), vector_store=GoldenVectorStore())
    report = evaluator.evaluate(make_state(case))
    feedback = report.feedbacks[0]
    expected_answer_state = case.get("answer_state", "answered")
    if "expected_score_min" in case:
        assert report.overall_score >= case["expected_score_min"]
    if "expected_score_max" in case:
        assert report.overall_score <= case["expected_score_max"]
    assert collect_report_quality_issues(report, expected_question_count=1) == []
    assert feedback.references
    assert feedback.references[0].chunk_id == case["expected_reference_chunk"]
    assert feedback.answer_state == expected_answer_state
    if expected_answer_state != "answered":
        assert feedback.score == 0
    for term in case.get("required_rationale_terms", []):
        assert contains_term(feedback.rationale.lower(), term)
    for term in case.get("required_critique_terms", []):
        assert contains_term(feedback.critique.lower(), term)


def test_golden_dataset_has_20_plus_cases():
    assert len(ALL_CASES) >= 20


def test_golden_dataset_includes_skipped_and_unanswered_cases():
    assert any(case.get("answer_state") == "skipped" for case in ALL_CASES)
    assert any(case.get("answer_state") == "unanswered" for case in ALL_CASES)
