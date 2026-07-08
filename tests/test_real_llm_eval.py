import os

import pytest

from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.llm import OpenAIInterviewLLM
from app.services.report_quality import collect_report_quality_issues
from tests.eval_support import GoldenVectorStore, load_all_cases, make_state


def _case_by_id(case_id: str) -> dict:
    return next(case for case in load_all_cases() if case["id"] == case_id)


@pytest.mark.real_llm
def test_real_llm_smoke_cases_pass_quality_gates():
    if os.getenv("RUN_REAL_LLM_EVAL") != "1":
        pytest.skip("Set RUN_REAL_LLM_EVAL=1 to enable real_llm smoke eval")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for real_llm smoke eval")

    llm = OpenAIInterviewLLM()
    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=GoldenVectorStore(),
    )

    for case_id in ["redis-strong-cache-aside", "redis-weak-basic-cache"]:
        report = evaluator.evaluate(make_state(_case_by_id(case_id)))
        issues = collect_report_quality_issues(report, expected_question_count=1)
        assert issues == [], f"{case_id}: {issues}"
        assert 0 <= report.overall_score <= 100
