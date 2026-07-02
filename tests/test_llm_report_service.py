from app.services.llm import OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewFeedback, InterviewReport


class FakeReportStructuredModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return InterviewReport(
            session_id="s1",
            overall_score=84,
            summary="Strong technical basics.",
            highlights=["Explained Redis fallback"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Please introduce a backend project.",
                    user_answer="The candidate described FastAPI and Redis.",
                    score=84,
                    critique="The answer needs clearer metrics.",
                    better_answer=(
                        "I built a FastAPI API with Redis cache, measured p95 "
                        "latency, and added database fallback."
                    ),
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


def test_openai_interview_llm_uses_structured_output_for_report():
    chat_model = FakeReportChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    plan = InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Please introduce a backend project.",
                focus="project communication",
            )
        ],
    )
    chunks = [
        {
            "question_id": "q1",
            "question_text": "Please introduce a backend project.",
            "focus": "project communication",
            "messages": [
                {
                    "role": "interviewer",
                    "content": "Please introduce a backend project.",
                },
                {
                    "role": "candidate",
                    "content": "I built a FastAPI service with Redis.",
                },
            ],
        }
    ]

    report = llm.generate_report(plan=plan, chunks=chunks, session_id="s1")

    assert report.overall_score == 84
    assert chat_model.schema is InterviewReport
    assert chat_model.method == "json_schema"
    assert "Backend interview" in chat_model.structured_model.last_prompt
    assert "I built a FastAPI service with Redis." in chat_model.structured_model.last_prompt
    assert "session_id: s1" in chat_model.structured_model.last_prompt
