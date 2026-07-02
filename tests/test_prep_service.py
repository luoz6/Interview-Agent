import pytest

from app.services.prep import InterviewPlan, InterviewQuestion, prepare_interview
from app.services.report import InterviewReport


class FakePlanLLM:
    def __init__(self):
        self.last_job_description = None
        self.last_resume_text = None

    def generate_plan(self, job_description: str, resume_text: str):
        self.last_job_description = job_description
        self.last_resume_text = resume_text
        return InterviewPlan(
            title="LLM 生成的后端模拟面试",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="请介绍一个最匹配岗位的项目。",
                    focus="项目匹配",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="请解释 Redis 缓存设计。",
                    focus="Redis",
                ),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="请设计一个后端服务。",
                    focus="系统设计",
                ),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "请继续展开。"


    def generate_report(
        self,
        plan: InterviewPlan,
        chunks: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Prep tests do not generate reports")


class FailingPlanLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise RuntimeError("llm failed")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise RuntimeError("llm failed")

    def generate_report(
        self,
        plan: InterviewPlan,
        chunks: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Prep tests do not generate reports")


def test_prepare_interview_uses_llm_for_question_plan():
    llm = FakePlanLLM()

    plan = prepare_interview(
        job_description="后端岗位，要求 Python、Redis、PostgreSQL。",
        resume_text="做过票务系统，使用 Redis 缓存。",
        llm=llm,
    )

    assert plan.title == "LLM 生成的后端模拟面试"
    assert len(plan.questions) == 3
    assert llm.last_job_description.startswith("后端岗位")
    assert llm.last_resume_text.startswith("做过票务系统")


def test_interview_plan_can_be_serialized_for_api():
    plan = prepare_interview(
        job_description="后端岗位，要求 Python 和 Redis。",
        resume_text="做过 Redis 缓存项目。",
        llm=FakePlanLLM(),
    )

    dumped = plan.model_dump()

    assert dumped["title"] == "LLM 生成的后端模拟面试"
    assert dumped["questions"][0]["prompt"]


def test_prepare_interview_falls_back_when_llm_fails():
    plan = prepare_interview(
        job_description="后端岗位，要求 Redis。",
        resume_text="做过缓存项目。",
        llm=FailingPlanLLM(),
    )

    assert plan.title == "基础模拟面试"
    assert len(plan.questions) == 3
    assert plan.questions[0].kind == "project"


def test_prepare_interview_rejects_empty_inputs():
    with pytest.raises(ValueError, match="job_description"):
        prepare_interview(job_description="", resume_text="做过后端项目。", llm=FakePlanLLM())

    with pytest.raises(ValueError, match="resume_text"):
        prepare_interview(job_description="后端岗位。", resume_text=" ", llm=FakePlanLLM())
