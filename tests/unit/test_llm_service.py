import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from app.services.context_budget import ContextBudgetExceeded
from app.services.llm import (
    LLMConfig,
    MissingLLMConfigError,
    OpenAIInterviewLLM,
    PLAN_MAX_LOGICAL_GENERATION_ROUNDS,
    PLAN_MAX_PROVIDER_INVOCATIONS,
    PLAN_MAX_QUALITY_REPAIR_ROUNDS,
    PLAN_MAX_TRANSPORT_ATTEMPTS,
    PLAN_QUALITY_REPAIR_PROMPT_SHA256,
    PLAN_QUALITY_REPAIR_PROMPT_TEMPLATE,
    PLAN_QUALITY_REPAIR_PROMPT_VERSION,
    PLAN_SDK_MAX_RETRIES,
    resolve_plan_output_mode,
)
from app.services.model_capabilities import ContextConfigurationError
from app.services.interview_plan_revision import PlanConfigurationSnapshot
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    PlanGenerationValidationError,
)
from app.services.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
)
from app.services.t65_production_capture import (
    install_t65_controlled_http_clients,
    shutdown_t65_controlled_http_clients_async,
    shutdown_t65_controlled_http_clients_sync,
)
from app.services.t65_provider_http_transport import T65ProviderTransportIdentity


def test_llm_config_reads_model_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "custom-model")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "32768")

    config = LLMConfig.from_env()

    assert config.api_key == "test-key"
    assert config.model == "custom-model"
    assert config.context_window_tokens == 32768


def test_llm_config_reads_bounded_provider_request_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "75")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")

    config = LLMConfig.from_env()

    assert config.request_timeout_seconds == 75
    assert config.max_retries == 0


def test_plan_generation_retry_and_round_constants_freeze_transport_ceiling():
    assert PLAN_MAX_LOGICAL_GENERATION_ROUNDS == 2
    assert PLAN_MAX_QUALITY_REPAIR_ROUNDS == 1
    assert PLAN_MAX_PROVIDER_INVOCATIONS == 4
    assert PLAN_SDK_MAX_RETRIES == 1
    assert PLAN_MAX_TRANSPORT_ATTEMPTS == 8
    assert PLAN_MAX_TRANSPORT_ATTEMPTS == PLAN_MAX_PROVIDER_INVOCATIONS * (
        PLAN_SDK_MAX_RETRIES + 1
    )


def test_llm_config_rejects_sdk_retry_count_above_frozen_ceiling(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")

    with pytest.raises(ValueError, match="at most 1"):
        LLMConfig.from_env()


def test_deepseek_production_config_resolves_single_request_plan_protocol(
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-pro")

    config = LLMConfig.from_env()

    assert config.plan_output_mode == "raw_only"
    assert resolve_plan_output_mode("deepseek-v4-pro") == "raw_only"
    assert resolve_plan_output_mode("deepseek-v4-flash") == "structured_first"
    assert resolve_plan_output_mode("deepseek-future") == "structured_first"
    assert resolve_plan_output_mode("other-model") == "structured_first"


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
    assert "http_client" not in captured
    assert "http_async_client" not in captured
    assert "http_socket_options" not in captured


def test_formal_t65_chat_model_requires_and_injects_both_controlled_clients(
    monkeypatch,
):
    captured = {}
    sync_client = object()
    async_client = object()

    monkeypatch.setenv("T65_PROVIDER_TRANSPORT_MODE", "builtin_production")
    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        SimpleNamespace(
            ChatOpenAI=lambda **kwargs: captured.update(kwargs) or object()
        ),
    )
    import app.services.t65_provider_http_transport as controlled

    monkeypatch.setattr(
        controlled,
        "get_t65_provider_http_clients",
        lambda: SimpleNamespace(
            sync_client=sync_client,
            async_client=async_client,
        ),
    )

    OpenAIInterviewLLM._build_chat_model(
        LLMConfig(
            api_key="test-key",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            max_retries=0,
        )
    )

    assert captured["http_client"] is sync_client
    assert captured["http_async_client"] is async_client
    assert captured["http_socket_options"] == ()


def test_production_installer_is_the_registry_seen_by_formal_llm(
    monkeypatch, tmp_path
):
    identity = T65ProviderTransportIdentity(
        run_id="llm-registry-integration",
        process_role="api",
        candidate_revision="a" * 40,
        candidate_tree="b" * 40,
        authorization_id="authorization-test",
        authorization_sha256="c" * 64,
        executor_sha256="d" * 64,
    )
    clients = install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=identity,
        expected_identity=identity,
    )
    captured = {}
    monkeypatch.setenv("T65_PROVIDER_TRANSPORT_MODE", "builtin_production")
    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        SimpleNamespace(ChatOpenAI=lambda **kwargs: captured.update(kwargs) or object()),
    )
    try:
        OpenAIInterviewLLM._build_chat_model(
            LLMConfig(
                api_key="test-key",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                max_retries=0,
            )
        )
        assert captured["http_client"] is clients.sync_client
        assert captured["http_async_client"] is clients.async_client
    finally:
        shutdown_t65_controlled_http_clients_sync()
        asyncio.run(shutdown_t65_controlled_http_clients_async())


@pytest.mark.parametrize(
    "config",
    [
        LLMConfig(
            api_key="test-key",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            max_retries=0,
        ),
        LLMConfig(
            api_key="test-key",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            max_retries=0,
        ),
        LLMConfig(
            api_key="test-key",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            max_retries=1,
        ),
    ],
)
def test_formal_t65_chat_model_rejects_model_endpoint_or_retry_drift(
    monkeypatch, config
):
    monkeypatch.setenv("T65_PROVIDER_TRANSPORT_MODE", "builtin_production")
    with pytest.raises(ContextConfigurationError):
        OpenAIInterviewLLM._build_chat_model(config)


def test_unknown_t65_transport_mode_fails_before_chat_model_creation(monkeypatch):
    monkeypatch.setenv("T65_PROVIDER_TRANSPORT_MODE", "diagnostic")
    with pytest.raises(ContextConfigurationError, match="unsupported"):
        OpenAIInterviewLLM._build_chat_model(
            LLMConfig(api_key="test-key", model="deepseek-v4-pro")
        )


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


class MeteredJsonMessage(FakeJsonMessage):
    usage_metadata = {
        "input_tokens": 120,
        "output_tokens": 30,
        "input_token_details": {"cache_read": 20},
    }
    response_metadata = {"model_name": "deepseek-v4-pro"}


class FallbackPlanChatModel:
    def __init__(self, content: str):
        self.content = content
        self.schema = None
        self.method = None
        self.last_prompt = None
        self.invoke_count = 0

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return FailingPlanStructuredModel()

    def invoke(self, prompt: str):
        self.invoke_count += 1
        self.last_prompt = prompt
        return FakeJsonMessage(self.content)


class MeteredRawPlanChatModel(FallbackPlanChatModel):
    def invoke(self, prompt: str):
        self.invoke_count += 1
        self.last_prompt = prompt
        return MeteredJsonMessage(self.content)


class FailingRawPlanChatModel(FallbackPlanChatModel):
    def invoke(self, prompt: str):
        self.invoke_count += 1
        self.last_prompt = prompt
        raise RuntimeError("provider unavailable")


class CountingFailingPlanStructuredModel:
    def __init__(self, owner):
        self.owner = owner

    def invoke(self, prompt: str):
        self.owner.structured_invoke_count += 1
        self.owner.prompts.append(prompt)
        raise RuntimeError("structured response unavailable")


class SequentialPlanChatModel:
    def __init__(self, contents: list[str], *, metered: bool = False):
        self.contents = list(contents)
        self.metered = metered
        self.schema = None
        self.method = None
        self.raw_invoke_count = 0
        self.structured_invoke_count = 0
        self.prompts: list[str] = []

    def with_structured_output(self, schema, method=None, include_raw=False):
        self.schema = schema
        self.method = method
        return CountingFailingPlanStructuredModel(self)

    def invoke(self, prompt: str):
        self.raw_invoke_count += 1
        self.prompts.append(prompt)
        content = self.contents.pop(0)
        message_type = MeteredJsonMessage if self.metered else FakeJsonMessage
        return message_type(content)


def serialized_plan(*, overloaded: bool) -> str:
    first_prompt = (
        "Explain the architecture, compare three alternatives, and describe "
        "the rollout and monitoring plan."
        if overloaded
        else "In your backend project, how did you diagnose a production cache failure?"
    )
    return json.dumps(
        {
            "title": "Bounded quality repair plan",
            "questions": [
                {
                    "id": "q1",
                    "kind": "project",
                    "prompt": first_prompt,
                    "focus": "project ownership",
                },
                {
                    "id": "q2",
                    "kind": "technical",
                    "prompt": (
                        "How would you preserve consistency when Redis becomes unavailable?"
                    ),
                    "focus": "cache consistency",
                },
                {
                    "id": "q3",
                    "kind": "system-design",
                    "prompt": "How would you scale your API under ten times traffic?",
                    "focus": "system scalability",
                },
            ],
        }
    )


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
    assert "self._invoke_raw_json_plan(" in source


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


def test_openai_interview_llm_raw_only_plan_skips_structured_request():
    chat_model = FallbackPlanChatModel(
        """
        {
          "title": "Single request backend interview",
          "questions": [
            {
              "id": "q1",
              "kind": "project",
              "prompt": "Explain your backend project.",
              "focus": "project ownership"
            },
            {
              "id": "q2",
              "kind": "technical",
              "prompt": "How do you keep cache data consistent?",
              "focus": "cache consistency"
            },
            {
              "id": "q3",
              "kind": "system-design",
              "prompt": "How would you scale the service?",
              "focus": "system scalability"
            }
          ]
        }
        """
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )

    plan = llm.generate_plan("Backend JD", "Backend resume")

    assert plan.title == "Single request backend interview"
    assert chat_model.schema is None
    assert chat_model.method is None
    assert "Return valid JSON only" in chat_model.last_prompt


def test_raw_only_plan_repairs_one_structurally_valid_hard_quality_failure():
    chat_model = SequentialPlanChatModel(
        [serialized_plan(overloaded=True), serialized_plan(overloaded=False)]
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )

    plan = llm.generate_plan("Backend JD", "Backend resume")

    assert plan.questions[0].prompt.startswith("In your backend project")
    assert chat_model.raw_invoke_count == 2
    assert chat_model.structured_invoke_count == 0


def test_plan_quality_repair_request_excludes_raw_sources_and_freezes_prompt():
    job_secret = "RAW-JD-SECRET-MARKER"
    resume_secret = "RAW-RESUME-SECRET-MARKER"
    chat_model = SequentialPlanChatModel(
        [serialized_plan(overloaded=True), serialized_plan(overloaded=False)]
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )

    llm.generate_plan(job_secret, resume_secret)

    assert len(chat_model.prompts) == 2
    initial_prompt, repair_prompt = chat_model.prompts
    assert job_secret in initial_prompt
    assert resume_secret in initial_prompt
    assert job_secret not in repair_prompt
    assert resume_secret not in repair_prompt
    assert PLAN_QUALITY_REPAIR_PROMPT_VERSION in repair_prompt
    assert '"code": "overloaded_multi_ask"' in repair_prompt
    assert (
        "Question contains multiple independently assessable asks."
        in repair_prompt
    )
    assert "Job description:" not in repair_prompt
    assert "Resume:" not in repair_prompt
    assert hashlib.sha256(
        PLAN_QUALITY_REPAIR_PROMPT_TEMPLATE.encode("utf-8")
    ).hexdigest() == PLAN_QUALITY_REPAIR_PROMPT_SHA256


def test_second_hard_quality_failure_returns_existing_quality_error_without_third_round():
    chat_model = SequentialPlanChatModel(
        [serialized_plan(overloaded=True), serialized_plan(overloaded=True)]
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )

    with pytest.raises(PlanGenerationValidationError) as rejected:
        llm.generate_plan("Backend JD", "Backend resume")

    assert rejected.value.code == "overloaded_multi_ask"
    assert str(rejected.value) == (
        "Question contains multiple independently assessable asks."
    )
    assert chat_model.raw_invoke_count == 2
    assert chat_model.structured_invoke_count == 0


def test_structured_fallback_quality_repair_never_exceeds_four_provider_invocations():
    provider_attempts = []
    chat_model = SequentialPlanChatModel(
        [serialized_plan(overloaded=True), serialized_plan(overloaded=False)],
        metered=True,
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            model="deepseek-v4-pro",
            plan_output_mode="structured_first",
        ),
        chat_model=chat_model,
        provider_attempt_hook=lambda: provider_attempts.append("started"),
    )
    reset_provider_context_metadata()

    plan = llm.generate_plan("Backend JD", "Backend resume")
    metadata = consume_provider_context_metadata()

    assert plan.questions[0].prompt.startswith("In your backend project")
    assert chat_model.structured_invoke_count == 2
    assert chat_model.raw_invoke_count == 2
    assert len(provider_attempts) == PLAN_MAX_PROVIDER_INVOCATIONS
    assert metadata["provider_attempt_count"] == PLAN_MAX_PROVIDER_INVOCATIONS
    assert metadata["provider_metered_attempt_count"] == 2
    assert metadata["provider_usage_available"] is False
    assert metadata["provider_input_tokens"] == 240
    assert metadata["provider_output_tokens"] == 60


def test_quality_repair_usage_accounts_for_every_application_invocation():
    chat_model = SequentialPlanChatModel(
        [serialized_plan(overloaded=True), serialized_plan(overloaded=False)],
        metered=True,
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )
    reset_provider_context_metadata()

    llm.generate_plan("Backend JD", "Backend resume")
    metadata = consume_provider_context_metadata()

    assert metadata["provider_attempt_count"] == 2
    assert metadata["provider_metered_attempt_count"] == 2
    assert metadata["provider_usage_available"] is True
    assert metadata["provider_input_tokens"] == 240
    assert metadata["provider_output_tokens"] == 60
    assert metadata["provider_cached_input_tokens"] == 40


def test_configured_plan_generation_uses_the_same_bounded_quality_repair_owner():
    configuration = PlanConfigurationSnapshot(
        difficulty="intermediate",
        target_duration_minutes=15,
        focus_preset="balanced",
        question_type_budget={
            "project": 1,
            "technical": 1,
            "system-design": 1,
        },
        expected_followup_budget=3,
        max_followups_per_question=2,
        generator_version="plan-generator-v2",
        followup_policy_version="fixed_v1",
    )
    chat_model = SequentialPlanChatModel(
        [serialized_plan(overloaded=True), serialized_plan(overloaded=False)]
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )

    plan = llm.generate_plan(
        "CONFIGURED-RAW-JD",
        "CONFIGURED-RAW-RESUME",
        configuration=configuration,
    )

    assert plan.questions[0].prompt.startswith("In your backend project")
    assert chat_model.raw_invoke_count == 2
    assert '"generator_version": "plan-generator-v2"' in chat_model.prompts[1]
    assert "CONFIGURED-RAW-JD" not in chat_model.prompts[1]
    assert "CONFIGURED-RAW-RESUME" not in chat_model.prompts[1]


def test_structurally_invalid_plan_fails_closed_without_quality_repair_round():
    payload = json.loads(serialized_plan(overloaded=False))
    payload["questions"].pop()
    chat_model = SequentialPlanChatModel([json.dumps(payload)])
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )

    with pytest.raises(ValueError, match="3 to 5 questions"):
        llm.generate_plan("Backend JD", "Backend resume")

    assert chat_model.raw_invoke_count == 1
    assert chat_model.structured_invoke_count == 0


def test_quality_repair_context_budget_failure_stops_before_second_provider_call(
    monkeypatch,
):
    chat_model = SequentialPlanChatModel([serialized_plan(overloaded=True)])
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )
    original_guard = llm._guard_prompt

    def reject_repair_prompt(prompt, policy, *, force_enforcement=False):
        if PLAN_QUALITY_REPAIR_PROMPT_VERSION in prompt:
            raise ContextBudgetExceeded(
                operation=policy.operation,
                estimated_input_tokens=2,
                available_input_tokens=1,
            )
        return original_guard(
            prompt,
            policy,
            force_enforcement=force_enforcement,
        )

    monkeypatch.setattr(llm, "_guard_prompt", reject_repair_prompt)

    with pytest.raises(ContextBudgetExceeded):
        llm.generate_plan("Backend JD", "Backend resume")

    assert chat_model.raw_invoke_count == 1
    assert chat_model.structured_invoke_count == 0


def test_raw_only_plan_publishes_one_complete_provider_usage_record():
    chat_model = MeteredRawPlanChatModel(
        """
        {
          "title": "Metered backend interview",
          "questions": [
            {"id": "q1", "kind": "project", "prompt": "Project?", "focus": "ownership"},
            {"id": "q2", "kind": "technical", "prompt": "Cache?", "focus": "consistency"},
            {"id": "q3", "kind": "system-design", "prompt": "Scale?", "focus": "scalability"}
          ]
        }
        """
    )
    provider_attempts = []
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            model="deepseek-v4-pro",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
        provider_attempt_hook=lambda: provider_attempts.append("started"),
    )
    reset_provider_context_metadata()

    llm.generate_plan("Backend JD", "Backend resume")
    metadata = consume_provider_context_metadata()

    assert provider_attempts == ["started"]
    assert metadata["provider_attempt_count"] == 1
    assert metadata["provider_metered_attempt_count"] == 1
    assert metadata["provider_usage_available"] is True
    assert metadata["provider_model"] == "deepseek-v4-pro"
    assert metadata["provider_input_tokens"] == 120
    assert metadata["provider_output_tokens"] == 30
    assert metadata["provider_cached_input_tokens"] == 20
    assert chat_model.invoke_count == 1


def test_raw_only_invalid_json_keeps_usage_and_never_sends_second_request():
    chat_model = MeteredRawPlanChatModel("not-json")
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            model="deepseek-v4-pro",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )
    reset_provider_context_metadata()

    with pytest.raises(ValueError, match="JSON"):
        llm.generate_plan("Backend JD", "Backend resume")
    metadata = consume_provider_context_metadata()

    assert chat_model.invoke_count == 1
    assert metadata["provider_attempt_count"] == 1
    assert metadata["provider_metered_attempt_count"] == 1
    assert metadata["provider_usage_available"] is True
    assert metadata["provider_model"] == "deepseek-v4-pro"
    assert metadata["provider_input_tokens"] == 120
    assert metadata["provider_output_tokens"] == 30
    assert metadata["provider_cached_input_tokens"] == 20


def test_raw_only_schema_failure_keeps_usage_and_never_sends_second_request():
    chat_model = MeteredRawPlanChatModel(
        """
        {
          "title": "Invalid schema plan",
          "questions": [
            {"id": "q1", "kind": "unsupported", "prompt": "Question?", "focus": "focus"}
          ]
        }
        """
    )
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            model="deepseek-v4-pro",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
    )
    reset_provider_context_metadata()

    with pytest.raises(ValueError, match="schema validation failed"):
        llm.generate_plan("Backend JD", "Backend resume")
    metadata = consume_provider_context_metadata()

    assert chat_model.invoke_count == 1
    assert metadata["provider_attempt_count"] == 1
    assert metadata["provider_metered_attempt_count"] == 1
    assert metadata["provider_usage_available"] is True
    assert metadata["provider_model"] == "deepseek-v4-pro"


def test_raw_only_provider_failure_records_one_unmetered_attempt_without_fallback():
    chat_model = FailingRawPlanChatModel("unused")
    provider_attempts = []
    llm = OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="injected-chat-model",
            model="deepseek-v4-pro",
            plan_output_mode="raw_only",
        ),
        chat_model=chat_model,
        provider_attempt_hook=lambda: provider_attempts.append("started"),
    )
    reset_provider_context_metadata()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        llm.generate_plan("Backend JD", "Backend resume")
    metadata = consume_provider_context_metadata()

    assert provider_attempts == ["started"]
    assert chat_model.invoke_count == 1
    assert metadata["provider_attempt_count"] == 1
    assert metadata.get("provider_metered_attempt_count", 0) == 0
    assert metadata.get("provider_usage_available") is not True


def test_invalid_plan_output_mode_is_rejected_before_chat_model_build(monkeypatch):
    build_calls = []
    monkeypatch.setattr(
        OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda _config: build_calls.append("called")),
    )

    with pytest.raises(ValueError, match="unsupported plan_output_mode"):
        OpenAIInterviewLLM(
            config=LLMConfig(
                api_key="test-key",
                plan_output_mode="invalid",  # type: ignore[arg-type]
            )
        )

    assert build_calls == []


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


def test_openai_interview_llm_followup_prompt_enforces_gap_constraints():
    chat_model = FakeFollowupChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    context = [
        {"role": "interviewer", "content": "How do you release a Redis lock?"},
        {"role": "candidate", "content": "I call delete."},
        {
            "role": "knowledge_gap",
            "content": '{"brief":{"target_signal":"owner token"}}',
        },
    ]

    llm.generate_followup(context)

    assert "knowledge_gap:" in chat_model.last_prompt
    assert "focus on the selected missing or incorrect signal" in chat_model.last_prompt
    assert "Do not reveal the complete expected answer" in chat_model.last_prompt
    assert "repeat the previous question" in chat_model.last_prompt
    assert "invent claims beyond bound evidence" in chat_model.last_prompt


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
