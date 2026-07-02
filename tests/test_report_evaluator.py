import pytest

from app.graphs.interview_state import build_initial_state
from app.services.evaluator import ShadowEvaluator
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationTimeout,
)


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Please introduce a backend project.",
                focus="project communication",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            ),
        ],
    )


def make_dimension_scores(score: int = 80) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_finished_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state["messages"].extend(
        [
            {
                "role": "candidate",
                "content": "I built a FastAPI service and used Redis for hot records.",
                "question_id": "q1",
            },
            {
                "role": "interviewer",
                "content": "How did you handle cache failure?",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "I used logical expiration, rate limiting, and database fallback.",
                "question_id": "q1",
            },
            {
                "role": "interviewer",
                "content": "Explain Redis cache invalidation.",
                "question_id": "q2",
            },
            {
                "role": "candidate",
                "content": (
                    "I delete cache after database updates and accept short "
                    "eventual consistency."
                ),
                "question_id": "q2",
            },
        ]
    )
    state["current_index"] = 2
    state["status"] = "finished"
    return state


class FakeReportLLM:
    def __init__(self):
        self.last_plan = None
        self.last_evaluation_items = None
        self.last_session_id = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Evaluator tests do not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError("Evaluator tests do not generate followups")

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        self.last_plan = plan
        self.last_evaluation_items = evaluation_items
        self.last_session_id = session_id
        return InterviewReport(
            session_id=session_id,
            overall_score=80,
            overall_dimension_scores=make_dimension_scores(80),
            summary="Clear project story with room for stronger metrics.",
            highlights=["Explained failure handling"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Please introduce a backend project.",
                    user_answer="The candidate described a FastAPI Redis project.",
                    score=82,
                    dimension_scores=make_dimension_scores(82),
                    rationale="The answer covered architecture and reliability tradeoffs.",
                    critique="Business metrics were not specific enough.",
                    better_answer=(
                        "I built a FastAPI service for hot record lookup, "
                        "measured p95 latency, and added Redis with database "
                        "fallback."
                    ),
                    references=[],
                ),
                InterviewFeedback(
                    question_id="q2",
                    question_text="Explain Redis cache invalidation.",
                    user_answer=(
                        "The candidate mentioned delete-after-update and "
                        "eventual consistency."
                    ),
                    score=78,
                    dimension_scores=make_dimension_scores(78),
                    rationale="The answer described the main write path but not the edge cases.",
                    critique="The answer did not explain race conditions.",
                    better_answer=(
                        "I would describe cache-aside, delete-after-write, "
                        "retry behavior, and consistency windows."
                    ),
                    references=[],
                ),
            ],
        )


class FailingReportLLM(FakeReportLLM):
    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise ValueError("invalid structured output")


class TimeoutReportLLM(FakeReportLLM):
    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise ReportGenerationTimeout("report generation timed out")


def test_evaluator_chunks_messages_by_question_id():
    llm = FakeReportLLM()
    evaluator = ShadowEvaluator(llm=llm)

    report = evaluator.evaluate(make_finished_state())

    assert report.overall_score == 80
    assert llm.last_session_id == "s1"
    assert llm.last_plan.title == "Backend interview"
    assert [item["question_id"] for item in llm.last_evaluation_items] == ["q1", "q2"]
    assert [message["role"] for message in llm.last_evaluation_items[0]["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
    ]


def test_evaluator_returns_fallback_completed_report_when_structured_output_fails():
    evaluator = ShadowEvaluator(llm=FailingReportLLM())

    report = evaluator.evaluate(make_finished_state())

    assert report.status == "completed"
    assert report.is_fallback is True
    assert report.overall_score == 60
    assert report.overall_dimension_scores.depth == 60
    assert (
        report.summary
        == "AI evaluation could not generate a complete report. Review the original answers manually."
    )
    assert len(report.feedbacks) == 2
    assert {feedback.question_id for feedback in report.feedbacks} == {"q1", "q2"}
    assert all(feedback.score == 60 for feedback in report.feedbacks)
    assert report.feedbacks[0].dimension_scores.engineering == 60
    assert report.feedbacks[0].references == []
    assert "fallback" in report.feedbacks[0].rationale.lower()


def test_evaluator_includes_unanswered_questions_in_fallback():
    state = make_finished_state()
    state["messages"] = [
        message
        for message in state["messages"]
        if message["question_id"] != "q2" or message["role"] != "candidate"
    ]
    evaluator = ShadowEvaluator(llm=FailingReportLLM())

    report = evaluator.evaluate(state)

    q2_feedback = next(
        feedback for feedback in report.feedbacks if feedback.question_id == "q2"
    )
    assert q2_feedback.user_answer == "No candidate answer was recorded for this question."


def test_evaluator_propagates_timeout_for_background_failure_state():
    evaluator = ShadowEvaluator(llm=TimeoutReportLLM())

    with pytest.raises(ReportGenerationTimeout):
        evaluator.evaluate(make_finished_state())
