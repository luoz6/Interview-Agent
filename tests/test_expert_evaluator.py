from app.graphs.interview_state import build_initial_state
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            )
        ],
    )


def make_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


class FakeVectorStore:
    def __init__(self):
        self.last_query = None

    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        self.last_query = (query_text, job_tags, source_types, limit)
        return [
            {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "content": "Delete cache after database writes and handle race conditions.",
                "source_type": "theory",
                "domain": "redis",
                "tags": ["redis"],
                "metadata": {"section": "consistency"},
                "score": 0.92,
            }
        ]


class FakeExpertLLM:
    def __init__(self):
        self.last_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        self.last_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=85,
            overall_dimension_scores=DimensionScores(
                breadth=84,
                depth=86,
                architecture=80,
                engineering=88,
                communication=87,
            ),
            summary="Strong Redis fundamentals with good practical tradeoffs.",
            highlights=["Explained cache invalidation tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="The candidate deletes cache after database writes.",
                    score=85,
                    dimension_scores=DimensionScores(
                        breadth=84,
                        depth=86,
                        architecture=80,
                        engineering=88,
                        communication=87,
                    ),
                    rationale=(
                        "The answer matched the retrieved Redis consistency guidance "
                        "but missed deeper race condition handling."
                    ),
                    critique="The answer did not explain retry or delayed double delete strategies.",
                    better_answer=(
                        "I would explain cache-aside, delete-after-write, race "
                        "conditions, and delayed cleanup."
                    ),
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


def test_expert_evaluator_injects_references_and_reports_progress():
    llm = FakeExpertLLM()
    vector_store = FakeVectorStore()
    evaluator = ExpertShadowEvaluator(llm=llm, vector_store=vector_store)
    progress_events: list[ReportProgress] = []

    report = evaluator.evaluate(make_state(), on_progress=progress_events.append)

    assert report.overall_score == 85
    assert vector_store.last_query[1] == ["python", "redis"]
    assert llm.last_items[0]["scoring_references"][0]["chunk_id"] == "redis-1"
    assert [event.stage for event in progress_events] == [
        "retrieving",
        "analyzing",
        "aggregating",
        "completed",
    ]
