from typing import Literal

from pydantic import BaseModel, Field

from app.services.llm import InterviewLLM


class InterviewQuestion(BaseModel):
    id: str = Field(description="题目唯一标识")
    kind: Literal["project", "technical", "system-design", "behavioral"] = Field(
        description="题目类型"
    )
    prompt: str = Field(description="面试官要问的问题")
    focus: str = Field(description="本题重点考察方向")


class InterviewPlan(BaseModel):
    title: str
    questions: list[InterviewQuestion]


def prepare_interview(
    job_description: str,
    resume_text: str,
    llm: InterviewLLM | None = None,
) -> InterviewPlan:
    job_description = _require_text("job_description", job_description)
    resume_text = _require_text("resume_text", resume_text)

    try:
        from app.agents.knowledge import KnowledgeAgent

        return KnowledgeAgent(llm=llm).generate_plan(
            job_description=job_description,
            resume_text=resume_text,
        )
    except Exception:
        return fallback_interview_plan()


def fallback_interview_plan() -> InterviewPlan:
    return InterviewPlan(
        title="基础模拟面试",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="请从简历中选择一个最能代表你能力的项目，说明业务背景、你的职责和最终结果。",
                focus="项目表达",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="请选择项目中一个核心技术点，说明你当时的设计取舍、失败场景和兜底方案。",
                focus="技术深度",
            ),
            InterviewQuestion(
                id="q3",
                kind="system-design",
                prompt="如果这个项目的流量扩大十倍，你会优先改造哪些模块？为什么？",
                focus="系统设计",
            ),
        ],
    )


def _require_text(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()

