import pytest

from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    build_prep_context,
    prepare_interview,
)
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


def test_build_prep_context_extracts_topics_and_question_hints():
    plan = InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Design a scalable FastAPI service.",
                focus="system design",
            ),
        ],
    )

    context = build_prep_context(
        job_description="Backend role using Python, FastAPI, Redis, MySQL, and Kafka.",
        resume_text="Built a FastAPI API with Redis cache and MySQL indexes.",
        job_tags=["python", "fastapi", "redis", "mysql", "kafka"],
        plan=plan,
    )

    assert context.summary == "Knowledge Agent 预热了 5 个岗位考点，并为 2 道题生成追问线索。"
    assert [topic.id for topic in context.topics] == [
        "topic-python",
        "topic-fastapi",
        "topic-redis",
        "topic-mysql",
        "topic-kafka",
    ]
    redis_topic = context.topics[2]
    assert redis_topic.label == "Redis"
    assert redis_topic.evidence == "JD 和简历同时命中 Redis，适合作为缓存一致性、穿透保护和高并发追问依据。"
    assert redis_topic.source == "jd_resume_keyword"
    assert context.question_hints[0].question_id == "q1"
    assert "topic-redis" in context.question_hints[0].topic_ids
    assert "追问缓存一致性、失效时机、穿透保护和降级兜底。" in context.question_hints[0].follow_up_hints
    assert context.question_hints[1].question_id == "q2"
    assert context.question_hints[1].topic_ids


def test_build_prep_context_uses_general_topic_when_tags_are_empty():
    plan = InterviewPlan(
        title="General interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce your project.",
                focus="project depth",
            )
        ],
    )

    context = build_prep_context(
        job_description="Backend role.",
        resume_text="Built internal tools.",
        job_tags=[],
        plan=plan,
    )

    assert [topic.id for topic in context.topics] == ["topic-general"]
    assert context.topics[0].label == "通用后端能力"
    assert context.question_hints[0].topic_ids == ["topic-general"]
