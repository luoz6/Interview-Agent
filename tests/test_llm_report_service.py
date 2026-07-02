from app.services.llm import OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)


class FakeReportStructuredModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return InterviewReport(
            session_id="s1",
            overall_score=84,
            overall_dimension_scores=DimensionScores(
                breadth=84,
                depth=84,
                architecture=84,
                engineering=84,
                communication=84,
            ),
            summary="Strong technical basics.",
            highlights=["Explained Redis fallback"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Please introduce a backend project.",
                    user_answer="The candidate described FastAPI and Redis.",
                    score=84,
                    dimension_scores=DimensionScores(
                        breadth=84,
                        depth=84,
                        architecture=84,
                        engineering=84,
                        communication=84,
                    ),
                    rationale="The answer covered the main cache strategy.",
                    critique="The answer needs clearer metrics.",
                    better_answer=(
                        "I built a FastAPI API with Redis cache and measured "
                        "p95 latency."
                    ),
                    references=[
                        FeedbackReference(
                            chunk_id="redis-1",
                            title="Redis cache consistency",
                            source_type="theory",
                            excerpt="Delete cache after database updates.",
                        )
                    ],
                )
            ],
        )


class FakeReportChatModel:
    def __init__(self):
        self.schema = None
        self.method = None
        self.structured_model = FakeReportStructuredModel()

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return self.structured_model


def test_generate_report_uses_interview_report_schema_and_includes_references():
    chat_model = FakeReportChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    plan = InterviewPlan(
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

    report = llm.generate_report(
        plan=plan,
        evaluation_items=[
            {
                "question_id": "q1",
                "question_text": "Explain Redis cache invalidation.",
                "focus": "Redis reliability",
                "messages": [
                    {
                        "role": "candidate",
                        "content": "I delete cache after database writes.",
                    }
                ],
                "scoring_references": [
                    {"chunk_id": "redis-1", "title": "Redis cache consistency"}
                ],
                "answer_references": [
                    {"chunk_id": "redis-2", "title": "High-score Redis answer"}
                ],
            }
        ],
        session_id="s1",
    )

    assert report.overall_score == 84
    assert chat_model.schema is InterviewReport
    assert chat_model.method == "json_schema"
    assert "Backend interview" in chat_model.structured_model.last_prompt
    assert "scoring_references" in chat_model.structured_model.last_prompt
    assert "answer_references" in chat_model.structured_model.last_prompt
    assert "session_id: s1" in chat_model.structured_model.last_prompt
    assert report.overall_dimension_scores.depth == 84
