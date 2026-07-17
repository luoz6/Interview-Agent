from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport


class FakeStage38InterviewLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        return make_stage38_plan()

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain how the cache failure path protects PostgreSQL."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain how "
        yield "the cache failure path protects PostgreSQL."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        return make_stage38_report(session_id)


def make_stage38_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Stage 38 backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="backend project",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="redis",
            ),
        ],
    )


def make_stage38_scores(score: int = 82) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_stage38_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=82,
        overall_dimension_scores=make_stage38_scores(),
        summary="Stage 38 deterministic report.",
        highlights=["Explained backend context."],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI API with Redis.",
                score=82,
                dimension_scores=make_stage38_scores(),
                rationale="The answer covered API and cache behavior.",
                critique="More production incident detail would help.",
                better_answer="Mention traffic, latency, failure handling, and data recovery.",
                references=[],
            )
        ],
    )
