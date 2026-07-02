import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.report import InterviewReport


class MissingLLMConfigError(RuntimeError):
    """LLM configuration is missing, usually OPENAI_API_KEY."""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "deepseekv4-pro"
    base_url: str | None = None
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingLLMConfigError("OPENAI_API_KEY is required")

        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "deepseekv4-pro"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.2")),
        )


class InterviewLLM(Protocol):
    def generate_plan(self, job_description: str, resume_text: str):
        """Generate the interview plan from JD and resume."""

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        """Generate a follow-up question from recent context."""

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        """Generate a structured expert report."""


class OpenAIInterviewLLM:
    def __init__(self, config: LLMConfig | None = None, chat_model=None) -> None:
        self.config = config
        self.chat_model = chat_model or self._build_chat_model(config or LLMConfig.from_env())

    def generate_plan(self, job_description: str, resume_text: str):
        from app.services.prep import InterviewPlan

        prompt = (
            "你是一个严格的技术面试官。请根据候选人的 JD 和简历生成一份结构化面试大纲。\n"
            "要求：\n"
            "1. 至少包含项目题、技术深挖题、系统设计题三类问题。\n"
            "2. 问题必须贴合候选人的真实经历，不要生成泛泛而谈的问题。\n"
            "3. 输出必须严格符合 InterviewPlan 的结构化字段。\n\n"
            f"JD:\n{job_description}\n\n"
            f"简历:\n{resume_text}"
        )
        structured_model = self.chat_model.with_structured_output(
            InterviewPlan,
            method="json_schema",
        )
        return structured_model.invoke(prompt)

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        transcript = "\n".join(
            f"{item['role']}: {item['content']}" for item in context if item.get("content")
        )
        prompt = (
            "你是一个有压迫感但专业的技术面试官。请根据下面最近几轮面试上下文，"
            "只生成一个犀利的追问。\n"
            "要求：\n"
            "1. 追问必须基于候选人刚才的回答。\n"
            "2. 优先追问取舍、边界条件、故障兜底、性能瓶颈或源码原理。\n"
            "3. 不要输出解释，不要输出多道题，只输出追问本身。\n\n"
            f"上下文:\n{transcript}"
        )
        message = self.chat_model.invoke(prompt)
        return str(getattr(message, "content", message)).strip()

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        from app.services.report import InterviewReport

        prompt = (
            "You are a strict technical interview coach. Generate a structured "
            "expert interview report.\n"
            "Rules:\n"
            "1. Use only the supplied transcript and retrieved references.\n"
            "2. Return one feedback item for every evaluation item.\n"
            "3. Scores must be integers from 0 to 100.\n"
            "4. dimension_scores must include breadth, depth, architecture, "
            "engineering, communication.\n"
            "5. rationale must explain the scoring and mention reference gaps "
            "when relevant.\n"
            "6. references must only include retrieved evidence.\n"
            "7. Keep highlights to one to three items.\n\n"
            f"session_id: {session_id}\n\n"
            f"plan_title: {plan.title}\n\n"
            "questions:\n"
            f"{json.dumps([question.model_dump() for question in plan.questions], ensure_ascii=False, indent=2)}\n\n"
            "evaluation_items:\n"
            f"{json.dumps(evaluation_items, ensure_ascii=False, indent=2)}"
        )
        structured_model = self.chat_model.with_structured_output(
            InterviewReport,
            method="json_schema",
        )
        return structured_model.invoke(prompt)

    @staticmethod
    def _build_chat_model(config: LLMConfig):
        from langchain_openai import ChatOpenAI

        kwargs = {
            "api_key": config.api_key,
            "model": config.model,
            "temperature": config.temperature,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)
