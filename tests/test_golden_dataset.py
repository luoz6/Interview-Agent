import json
from pathlib import Path

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


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Golden backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            )
        ],
    )


def make_state(answer: str):
    state = build_initial_state(
        session_id="golden-s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": answer,
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


class GoldenVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        return [
            {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "content": "Delete cache after database writes and handle race conditions. Keep fallback behavior and watch latency metrics.",
                "source_type": "theory",
                "domain": "redis",
                "tags": ["redis"],
                "metadata": {"section": "consistency"},
                "score": 0.95,
            }
        ]


class GoldenLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        answer = evaluation_items[0]["messages"][1]["content"].lower()
        strong = "race conditions" in answer and "fallback" in answer and "p95 latency" in answer
        score = 88 if strong else 58
        rationale = (
            "Based on Redis cache consistency guidance, the answer covered delete-after-write, "
            "race conditions, fallback, and p95 latency."
            if strong
            else "Based on Redis cache consistency guidance, the answer missed race conditions, fallback, and consistency details."
        )
        critique = (
            "The answer is strong but could add more implementation detail."
            if strong
            else "The answer missed race conditions, fallback, and consistency details."
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
                    question_text="Explain Redis cache invalidation.",
                    user_answer=evaluation_items[0]["messages"][1]["content"],
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
                    better_answer="Explain cache-aside, delete-after-write, race conditions, fallback, and metrics.",
                    references=[
                        FeedbackReference(
                            chunk_id="redis-1",
                            title="Redis cache consistency",
                            source_type="theory",
                            excerpt="Delete cache after database writes and handle race conditions.",
                        )
                    ],
                )
            ],
        )


def load_case(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def test_golden_strong_answer_scores_high_with_reference():
    case = load_case("redis_strong_answer.json")
    evaluator = ExpertShadowEvaluator(llm=GoldenLLM(), vector_store=GoldenVectorStore())

    report = evaluator.evaluate(make_state(case["answer"]))

    feedback = report.feedbacks[0]
    assert report.overall_score >= 80
    assert feedback.references
    assert feedback.references[0].chunk_id == "redis-1"
    assert "race conditions" in feedback.rationale.lower()


def test_golden_weak_answer_scores_lower_and_calls_out_missing_signals():
    case = load_case("redis_weak_answer.json")
    evaluator = ExpertShadowEvaluator(llm=GoldenLLM(), vector_store=GoldenVectorStore())

    report = evaluator.evaluate(make_state(case["answer"]))

    feedback = report.feedbacks[0]
    assert report.overall_score < 70
    assert "race conditions" in feedback.critique.lower()
    assert "fallback" in feedback.critique.lower()
    assert feedback.references[0].chunk_id == "redis-1"
