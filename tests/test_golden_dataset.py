import json
from pathlib import Path

import pytest

from app.graphs.interview_state import build_initial_state
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
REFERENCE_FIXTURES = {
    "redis": {
        "chunk_id": "redis-1",
        "title": "Redis cache consistency",
        "content": "Delete cache after database writes and handle race conditions. Keep fallback behavior and watch latency metrics.",
        "source_type": "theory",
        "domain": "redis",
        "tags": ["redis"],
        "metadata": {"section": "consistency"},
        "score": 0.95,
    },
    "mysql": {
        "chunk_id": "mysql-1",
        "title": "MySQL performance baselines",
        "content": "Use EXPLAIN, add appropriate indexes, keep transactions short, and watch lock wait plus latency metrics.",
        "source_type": "theory",
        "domain": "mysql",
        "tags": ["mysql"],
        "metadata": {"section": "performance"},
        "score": 0.94,
    },
    "kafka": {
        "chunk_id": "kafka-1",
        "title": "Kafka consumer reliability",
        "content": "Keep consumers idempotent, use retries and dead-letter handling, and monitor consumer lag closely.",
        "source_type": "theory",
        "domain": "kafka",
        "tags": ["kafka"],
        "metadata": {"section": "reliability"},
        "score": 0.93,
    },
    "system-design": {
        "chunk_id": "system-design-1",
        "title": "System design review rubric",
        "content": "Define service boundaries, cover failure isolation, and validate the design with latency and saturation metrics.",
        "source_type": "theory",
        "domain": "system-design",
        "tags": ["system-design"],
        "metadata": {"section": "architecture"},
        "score": 0.96,
    },
}
DOMAIN_SIGNALS = {
    "redis": {
        "strong_terms": ["race conditions", "fallback", "p95 latency"],
        "missing_terms": ["race conditions", "fallback", "consistency"],
        "better_answer": "Explain cache-aside, delete-after-write, race conditions, fallback, and metrics.",
    },
    "mysql": {
        "strong_terms": ["indexes", "transactions", "p95 latency"],
        "missing_terms": ["indexes", "transactions", "lock wait"],
        "better_answer": "Explain query plans, indexes, short transactions, and lock plus latency metrics.",
    },
    "kafka": {
        "strong_terms": ["idempotent", "dead-letter", "consumer lag"],
        "missing_terms": ["idempotent", "dead-letter", "consumer lag"],
        "better_answer": "Explain idempotent consumers, offset commit strategy, retries, dead-letter handling, and lag monitoring.",
    },
    "system-design": {
        "strong_terms": ["service boundaries", "failure isolation", "latency"],
        "missing_terms": ["service boundaries", "failure isolation", "latency"],
        "better_answer": "Explain service boundaries, data flow, failure isolation, and observable latency tradeoffs.",
    },
}


def make_plan(question: str, focus: str) -> InterviewPlan:
    return InterviewPlan(
        title="Golden backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt=question,
                focus=focus,
            )
        ],
    )


def make_state(case: dict):
    state = build_initial_state(
        session_id="golden-s1",
        plan=make_plan(case["question"], case["focus"]),
        job_description=f"Backend role focused on {case['domain']}.",
        resume_text=f"Built production systems related to {case['domain']}.",
        job_tags=case["job_tags"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": case["answer"],
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


def contains_term(text: str, term: str) -> bool:
    if term == "service boundaries":
        return "service boundaries" in text or "service boundary" in text
    if term == "failure isolation":
        return "failure isolation" in text or "isolate failures" in text or "isolating failures" in text
    return term in text


class GoldenVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        lowered_query = query_text.lower()
        if "system design" in lowered_query or "architecture" in lowered_query:
            return [REFERENCE_FIXTURES["system-design"]]
        for tag in job_tags:
            if tag in REFERENCE_FIXTURES:
                return [REFERENCE_FIXTURES[tag]]
        for domain, reference in REFERENCE_FIXTURES.items():
            if domain in lowered_query:
                return [reference]
        return [REFERENCE_FIXTURES["system-design"]]


class GoldenLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        item = evaluation_items[0]
        answer = item["messages"][1]["content"].lower()
        reference = item["scoring_references"][0]
        domain = reference["domain"]
        domain_signals = DOMAIN_SIGNALS[domain]
        strong = all(contains_term(answer, term) for term in domain_signals["strong_terms"])
        score = 88 if strong else 58
        rationale = (
            f"Based on {reference['title']} guidance, the answer covered "
            + ", ".join(domain_signals["strong_terms"])
            + "."
            if strong
            else f"Based on {reference['title']} guidance, the answer missed "
            + ", ".join(domain_signals["missing_terms"])
            + "."
        )
        critique = (
            "The answer is strong but could add more implementation detail."
            if strong
            else "The answer missed " + ", ".join(domain_signals["missing_terms"]) + "."
        )
        return InterviewReport(
            session_id=session_id,
            overall_score=score,
            overall_dimension_scores=DimensionScores(
                breadth=score,
                depth=score,
                architecture=score,
                engineering=score,
                communication=score,
            ),
            summary="Golden dataset evaluation.",
            highlights=["Grounded in retrieved Redis guidance"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text=item["question_text"],
                    user_answer=item["messages"][1]["content"],
                    score=score,
                    dimension_scores=DimensionScores(
                        breadth=score,
                        depth=score,
                        architecture=score,
                        engineering=score,
                        communication=score,
                    ),
                    rationale=rationale,
                    critique=critique,
                    better_answer=domain_signals["better_answer"],
                    references=[
                        FeedbackReference(
                            chunk_id=reference["chunk_id"],
                            title=reference["title"],
                            source_type=reference["source_type"],
                            excerpt=reference["content"],
                        )
                    ],
                )
            ],
        )


def load_case(name: str) -> list[dict]:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def load_all_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(GOLDEN_DIR.glob("*_cases.json")):
        cases.extend(load_case(path.name))
    return cases


ALL_CASES = load_all_cases()


@pytest.mark.parametrize("case", ALL_CASES, ids=[case["id"] for case in ALL_CASES])
def test_golden_dataset_cases(case: dict):
    evaluator = ExpertShadowEvaluator(llm=GoldenLLM(), vector_store=GoldenVectorStore())
    report = evaluator.evaluate(make_state(case))
    feedback = report.feedbacks[0]
    if "expected_score_min" in case:
        assert report.overall_score >= case["expected_score_min"]
    if "expected_score_max" in case:
        assert report.overall_score <= case["expected_score_max"]
    assert feedback.references
    assert feedback.references[0].chunk_id == case["expected_reference_chunk"]
    for term in case.get("required_rationale_terms", []):
        assert contains_term(feedback.rationale.lower(), term)
    for term in case.get("required_critique_terms", []):
        assert contains_term(feedback.critique.lower(), term)


def test_golden_dataset_has_20_plus_cases():
    assert len(ALL_CASES) >= 20
