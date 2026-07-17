import sys
from types import SimpleNamespace

import pytest

from app.services.llm import LLMConfig, MissingLLMConfigError, OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion


def test_llm_config_reads_model_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "custom-model")

    config = LLMConfig.from_env()

    assert config.api_key == "test-key"
    assert config.model == "custom-model"


def test_llm_config_reads_bounded_provider_request_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "75")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")

    config = LLMConfig.from_env()

    assert config.request_timeout_seconds == 75
    assert config.max_retries == 0


def test_chat_model_receives_timeout_and_retry_settings(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        SimpleNamespace(ChatOpenAI=fake_chat_openai),
    )

    OpenAIInterviewLLM._build_chat_model(
        LLMConfig(
            api_key="test-key",
            request_timeout_seconds=45,
            max_retries=0,
        )
    )

    assert captured["timeout"] == 45
    assert captured["max_retries"] == 0


def test_report_output_mode_can_be_selected_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_REPORT_OUTPUT_MODE", "raw_only")

    llm = OpenAIInterviewLLM(chat_model=FakeChatModel())

    assert llm.report_output_mode == "raw_only"


def test_llm_config_uses_deepseek_default_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = LLMConfig.from_env()

    assert config.model == "deepseek-v4-pro"


def test_llm_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(MissingLLMConfigError, match="OPENAI_API_KEY"):
        LLMConfig.from_env()


class FakeStructuredModel:
    def invoke(self, prompt: str):
        return InterviewPlan(
            title="LLM generated mock interview",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="介绍项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="解释 Redis。", focus="Redis"),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="设计服务。",
                    focus="系统设计",
                ),
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


class FailingPlanStructuredModel:
    def invoke(self, prompt: str):
        raise RuntimeError(
            "Error code: 400 - {'error': {'message': 'This response_format type is unavailable now'}}"
        )


class FakeJsonMessage:
    def __init__(self, content: str):
        self.content = content


class FallbackPlanChatModel:
    def __init__(self, content: str):
        self.content = content
        self.schema = None
        self.method = None
        self.last_prompt = None

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return FailingPlanStructuredModel()

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return FakeJsonMessage(self.content)


def test_openai_interview_llm_uses_structured_output_for_plan():
    chat_model = FakeChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    plan = llm.generate_plan("后端 JD", "后端简历")

    assert plan.title == "LLM generated mock interview"
    assert chat_model.schema is InterviewPlan
    assert chat_model.method == "json_schema"


def test_openai_plan_prompt_accepts_safe_knowledge_context():
    llm = OpenAIInterviewLLM(chat_model=FakeChatModel())

    prompt = llm._build_plan_prompt(
        job_description="Backend JD",
        resume_text="Backend resume",
        knowledge_context=[
            {
                "evidence_id": "redis_consistency",
                "title": "Redis Cache Consistency",
                "candidate_summary": "Consistency mechanism interview evidence.",
            }
        ],
    )

    assert "Trusted knowledge candidates" in prompt
    assert "redis_consistency" in prompt
    assert "Redis Cache Consistency" in prompt
    assert "do not invent evidence IDs" in prompt


def test_openai_interview_llm_generate_plan_has_no_unreachable_legacy_prompt():
    import inspect

    source = inspect.getsource(OpenAIInterviewLLM.generate_plan)

    assert "return structured_model.invoke(prompt)" not in source
    assert "structured_model = self.chat_model.with_structured_output" not in source
    assert "self._invoke_structured_plan(prompt, InterviewPlan)" in source
    assert "self._invoke_raw_json_plan(prompt)" in source


def test_openai_interview_llm_falls_back_to_json_for_plan_when_structured_output_fails():
    chat_model = FallbackPlanChatModel(
        """
        ```json
        {
          "title": "DeepSeek compatible backend interview",
          "questions": [
            {
              "id": "q1",
              "kind": "project",
              "prompt": "Explain your FastAPI backend project and your role.",
              "focus": "project ownership"
            },
            {
              "id": "q2",
              "kind": "technical",
              "prompt": "How do you keep Redis cache data consistent?",
              "focus": "redis consistency"
            },
            {
              "id": "q3",
              "kind": "system-design",
              "prompt": "How would you scale the service under 10x traffic?",
              "focus": "system scalability"
            }
          ]
        }
        ```
        """
    )
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    plan = llm.generate_plan("Backend JD", "FastAPI Redis resume")

    assert plan.title == "DeepSeek compatible backend interview"
    assert [question.id for question in plan.questions] == ["q1", "q2", "q3"]
    assert chat_model.schema is InterviewPlan
    assert chat_model.method == "json_schema"
    assert "Return valid JSON only" in chat_model.last_prompt
    assert "FastAPI Redis resume" in chat_model.last_prompt


def test_openai_interview_llm_rejects_invalid_json_plan_fallback():
    chat_model = FallbackPlanChatModel(
        """
        {
          "title": "bad",
          "questions": [
            {
              "id": "q1",
              "kind": "invalid",
              "prompt": "Explain a project.",
              "focus": "project"
            }
          ]
        }
        """
    )
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    with pytest.raises(ValueError, match="raw interview plan JSON schema validation failed"):
        llm.generate_plan("Backend JD", "FastAPI Redis resume")


class FakeMessage:
    content = "你提到了 Redis，请说明如果 Redis 宕机，系统如何降级。"


class FakeChunk:
    def __init__(self, content: str):
        self.content = content


class FakeFollowupChatModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return FakeMessage()

    def stream(self, prompt: str):
        self.last_prompt = prompt
        yield FakeChunk("你提到了 Redis，")
        yield FakeChunk("请说明如果 Redis 宕机，")
        yield FakeChunk("系统如何降级。")


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


def test_openai_interview_llm_followup_prompt_includes_knowledge_guidance():
    chat_model = FakeFollowupChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    context = [
        {"role": "interviewer", "content": "Explain Redis cache invalidation."},
        {"role": "candidate", "content": "I delete cache after DB writes."},
        {
            "role": "knowledge_agent",
            "content": "Prep guidance for q1: focus topics Redis. Suggested follow-up angles: 追问缓存一致性。",
        },
        {
            "role": "knowledge_evidence",
            "content": "Evidence for q1 [id=redis_consistency]: cache race reference.",
        },
    ]

    llm.generate_followup(context)

    assert "knowledge_agent: Prep guidance for q1" in chat_model.last_prompt
    assert (
        "Use knowledge_agent entries as interview guidance, not as candidate answers."
        in chat_model.last_prompt
    )
    assert "knowledge_evidence: Evidence for q1" in chat_model.last_prompt
    assert (
        "Use knowledge_evidence entries only as reference material, never as candidate answers."
        in chat_model.last_prompt
    )


def test_openai_interview_llm_streams_followup_from_context():
    chat_model = FakeFollowupChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    context = [
        {"role": "interviewer", "content": "请介绍 Redis 缓存方案。"},
        {"role": "candidate", "content": "我用 Redis 缓存热点数据。"},
    ]

    chunks = list(llm.stream_followup(context))

    assert chunks == [
        "你提到了 Redis，",
        "请说明如果 Redis 宕机，",
        "系统如何降级。",
    ]
