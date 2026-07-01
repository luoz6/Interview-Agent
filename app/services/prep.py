from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class InterviewQuestion:
    id: str
    kind: str
    prompt: str
    focus: str


@dataclass(frozen=True)
class InterviewPlan:
    title: str
    questions: List[InterviewQuestion]


TECH_KEYWORDS = [
    "Python",
    "FastAPI",
    "Redis",
    "PostgreSQL",
    "SQL",
    "cache",
    "database",
    "API",
]


def prepare_interview(job_description: str, resume_text: str) -> InterviewPlan:
    job_description = _require_text("job_description", job_description)
    resume_text = _require_text("resume_text", resume_text)

    role_title = _infer_role_title(job_description)
    keywords = _extract_keywords(f"{job_description} {resume_text}")
    primary_keyword = keywords[0] if keywords else "后端开发"

    questions = [
        InterviewQuestion(
            id="q1",
            kind="project",
            prompt=f"请从简历里选择一个最匹配{role_title}的项目，说明业务背景、你的职责和最终结果。",
            focus="项目匹配度",
        ),
        InterviewQuestion(
            id="q2",
            kind="technical",
            prompt=f"你的材料中提到了 {primary_keyword}。请说明你在设计时做过哪些取舍，以及可能出现哪些失败场景。",
            focus=primary_keyword,
        ),
        InterviewQuestion(
            id="q3",
            kind="system-design",
            prompt="请设计一个适合该岗位的小型服务，重点说明存储设计、接口边界和异常处理。",
            focus="系统设计",
        ),
    ]

    if "Redis" in keywords:
        questions.insert(
            2,
            InterviewQuestion(
                id="q-redis",
                kind="technical",
                prompt="你的材料里出现了 Redis。请分别说明缓存穿透、缓存击穿和缓存雪崩的处理思路。",
                focus="Redis",
            ),
        )

    return InterviewPlan(title=f"{role_title}模拟面试", questions=questions)


def _require_text(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _infer_role_title(job_description: str) -> str:
    lower = job_description.lower()
    if "backend engineer" in lower:
        return "后端工程师"
    if "backend role" in lower:
        return "后端岗位"
    if "frontend" in lower:
        return "前端岗位"
    if "full stack" in lower or "full-stack" in lower:
        return "全栈岗位"
    return "软件工程师岗位"


def _extract_keywords(text: str) -> List[str]:
    lowered = text.lower()
    found = []
    for keyword in TECH_KEYWORDS:
        if keyword.lower() in lowered and keyword not in found:
            found.append(keyword)
    return found
