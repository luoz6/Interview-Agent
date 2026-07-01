import pytest

from app.services.llm import LLMConfig, MissingLLMConfigError, OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion


def test_llm_config_reads_model_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "custom-model")

    config = LLMConfig.from_env()

    assert config.api_key == "test-key"
    assert config.model == "custom-model"


def test_llm_config_uses_deepseek_default_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = LLMConfig.from_env()

    assert config.model == "deepseekv4-pro"


def test_llm_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(MissingLLMConfigError, match="OPENAI_API_KEY"):
        LLMConfig.from_env()


class FakeStructuredModel:
    def invoke(self, prompt: str):
        return InterviewPlan(
            title="LLM 生成的模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="介绍项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="解释 Redis。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="设计服务。", focus="系统设计"),
            ],
        )


class FakeChatModel:
    def __init__(self):
        self.schema = None
        self.method = None

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return FakeStructuredModel()


def test_openai_interview_llm_uses_structured_output_for_plan():
    chat_model = FakeChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    plan = llm.generate_plan("后端 JD", "后端简历")

    assert plan.title == "LLM 生成的模拟面试"
    assert chat_model.schema is InterviewPlan
    assert chat_model.method == "json_schema"


class FakeMessage:
    content = "你提到了 Redis，请说明如果 Redis 宕机，系统如何降级。"


class FakeFollowupChatModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return FakeMessage()


def test_openai_interview_llm_generates_followup_from_context():
    chat_model = FakeFollowupChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    context = [
        {"role": "interviewer", "content": "请介绍 Redis 缓存方案。"},
        {"role": "candidate", "content": "我用 Redis 缓存热点数据。"},
    ]

    followup = llm.generate_followup(context)

    assert "Redis 宕机" in followup
    assert "请介绍 Redis 缓存方案" in chat_model.last_prompt
    assert "我用 Redis 缓存热点数据" in chat_model.last_prompt
