from app.agents.examiner import ExaminerAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.report_coach import ReportCoachAgent
from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport


class FollowupLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Why did you choose delete-after-write instead of write-through?"

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Why did you choose delete-after-write instead of write-through?"

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        raise AssertionError("not used")


class PlanLLM(FollowupLLM):
    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="Backend plan",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis cache invalidation.",
                    focus="Redis consistency",
                )
            ],
        )


class ReportLLM(FollowupLLM):
    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        return InterviewReport(
            session_id=session_id,
            overall_score=82,
            overall_dimension_scores=DimensionScores(
                breadth=80,
                depth=82,
                architecture=81,
                engineering=84,
                communication=83,
            ),
            summary="Solid answer with one consistency gap.",
            highlights=["Explained cache-aside"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="Delete cache after database update.",
                    score=82,
                    dimension_scores=DimensionScores(
                        breadth=80,
                        depth=82,
                        architecture=81,
                        engineering=84,
                        communication=83,
                    ),
                    rationale="The answer covered delete-after-write.",
                    critique="It missed race condition handling.",
                    better_answer="Mention delayed double delete and retry.",
                    references=[],
                )
            ],
        )


class VectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        return []


def test_examiner_agent_generates_followup_from_context():
    agent = ExaminerAgent(llm=FollowupLLM())

    follow_up = agent.generate_followup(
        context=[{"role": "candidate", "content": "I delete cache after DB writes."}],
        focus="Redis consistency",
    )

    assert follow_up == "Why did you choose delete-after-write instead of write-through?"


def test_examiner_agent_falls_back_when_llm_fails():
    class FailingLLM(FollowupLLM):
        def generate_followup(self, context: list[dict[str, str]]) -> str:
            raise RuntimeError("provider down")

    agent = ExaminerAgent(llm=FailingLLM())

    assert agent.generate_followup(context=[], focus="Redis consistency") == (
        "请继续深挖 Redis consistency：你当时做了什么取舍，为什么这样选？"
    )


def test_knowledge_agent_generates_plan():
    plan = KnowledgeAgent(llm=PlanLLM()).generate_plan(
        job_description="Backend Redis role",
        resume_text="Built Redis cache",
    )

    assert plan.title == "Backend plan"
    assert plan.questions[0].focus == "Redis consistency"


def test_report_coach_agent_generates_report():
    plan = PlanLLM().generate_plan("jd", "resume")
    report = ReportCoachAgent(llm=ReportLLM()).generate_report(
        plan=plan,
        evaluation_items=[],
        session_id="s1",
    )

    assert report.session_id == "s1"
    assert report.feedbacks[0].question_id == "q1"


def test_shadow_reviewer_agent_wraps_expert_evaluator():
    agent = ShadowReviewerAgent(llm=ReportLLM(), vector_store=VectorStore())

    assert agent.llm is not None
    assert agent.vector_store is not None
    assert agent._evaluator is not None
