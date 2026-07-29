from hashlib import sha256

import pytest

from app.agents.context_compressor import ContextCompressorAgent
from app.services.agent_runtime import AgentExecutionContext, AgentExecutionRunner
from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextCompressionPolicy,
    compressor_settings_sha256,
)
from app.services.context_budget import ContextBudgetExceeded
from app.services.context_compression import (
    OpenAIContextCompressor,
    compressor_config_from_llm,
)
from app.services.llm import LLMConfig


class FakeStructuredModel:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []
        self.max_tokens = None
        self.schema = None

    def with_structured_output(self, schema, method):
        assert method == "json_schema"
        self.schema = schema
        return self

    def bind(self, *, max_tokens):
        self.max_tokens = max_tokens
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.payload


class Recorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def make_contract(*, input_cap=2048):
    content = "Candidate used idempotency for retry safety."
    digest = sha256(content.encode("utf-8")).hexdigest()
    source = CompressionSourceSegment(
        segment_index=0,
        segment_type="conversation_message",
        content=content,
        content_sha256=digest,
    )
    policy = ContextCompressionPolicy(
        artifact_type="question_conversation",
        policy_version="conversation-v1",
        prompt_contract_version="prompt-v1",
        output_schema_version="question-conversation-v1",
        compressor_operation="context_compressor.question_conversation",
        compressor_input_cap_tokens=input_cap,
        target_output_tokens=256,
        max_output_units=4,
        max_supporting_excerpt_tokens=64,
    )
    question_digest = "6" * 64
    payload = {
        "schema_version": "question-conversation-v1",
        "question_id_sha256": question_digest,
        "units": [],
        "unresolved_topics": [],
        "source_message_count": 1,
    }
    return policy, [source], question_digest, payload


def make_provider(chat_model, *, context_window=8192):
    return OpenAIContextCompressor(
        llm_config=LLMConfig(
            api_key="test-key",
            model="gpt-4o",
            base_url="https://provider.example/v1/",
            context_window_tokens=context_window,
        ),
        chat_model=chat_model,
    )


def test_compressor_config_identity_is_non_secret_and_behavior_complete():
    config = compressor_config_from_llm(
        LLMConfig(
            api_key="secret-key",
            model="gpt-4o",
            base_url="https://provider.example/v1/",
            temperature=0.1,
            request_timeout_seconds=45,
            max_retries=2,
            tokenizer_family="cl100k_base",
        )
    )

    assert config.base_url_identity == "https://provider.example/v1"
    assert config.request_timeout_seconds == 45
    assert config.max_retries == 2
    assert "secret-key" not in compressor_settings_sha256(config)


def test_structured_compressor_keeps_fixed_instructions_first_and_binds_output():
    policy, sources, question_digest, payload = make_contract()
    chat = FakeStructuredModel(payload)
    provider = make_provider(chat)

    result = provider.compress(
        policy=policy,
        source_segments=sources,
        expected_question_id_sha256=question_digest,
    )

    assert result == payload
    assert chat.max_tokens == policy.target_output_tokens
    assert chat.prompts[0].startswith(
        "You are a deterministic context compressor."
    )
    assert chat.prompts[0].index("source_segments=") > chat.prompts[0].index(
        "Return only the requested JSON schema."
    )


def test_final_rendered_prompt_overflow_fails_before_provider_call():
    policy, sources, question_digest, payload = make_contract(input_cap=1)
    chat = FakeStructuredModel(payload)
    provider = make_provider(chat)

    with pytest.raises(ContextBudgetExceeded):
        provider.compress(
            policy=policy,
            source_segments=sources,
            expected_question_id_sha256=question_digest,
        )

    assert chat.prompts == []


def test_agent_has_no_business_fallback_and_emits_only_safe_metadata():
    policy, sources, question_digest, payload = make_contract()
    chat = FakeStructuredModel(payload)
    provider = make_provider(chat)
    recorder = Recorder()
    agent = ContextCompressorAgent(
        provider=provider,
        execution_runner=AgentExecutionRunner(recorder=recorder),
    )

    result = agent.compress(
        policy=policy,
        source_segments=sources,
        expected_question_id_sha256=question_digest,
        execution_context=AgentExecutionContext(
            correlation_id="correlation-1",
            agent="context_compressor",
            operation=policy.compressor_operation,
            phase="interview",
        ),
    )

    assert result == payload
    assert recorder.records[0].status == "completed"
    metadata = recorder.records[0].safe_metadata
    assert metadata["artifact_type"] == "question_conversation"
    assert metadata["context_policy_version"] == "conversation-v1"
    assert metadata["source_segment_count"] == 1
    assert metadata["target_output_tokens"] == 256
    assert metadata["provider_attempt_count"] == 1
    assert metadata["provider_usage_available"] is False
    assert set(metadata) == {
        "artifact_type",
        "context_policy_version",
        "source_segment_count",
        "target_output_tokens",
        "estimated_input_tokens",
        "available_input_tokens",
        "budget_utilization_basis_points",
        "estimator_path",
        "estimator_fallback_used",
        "provider_attempt_count",
        "provider_usage_available",
    }
