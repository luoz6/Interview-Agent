from __future__ import annotations

import json
from typing import Any, Sequence

from app.domain.context.artifacts import (
    CompressionSourceSegment,
    ContextCompressorConfig,
    ContextCompressionPolicy,
    EvidenceCompressionArtifact,
    PrepContextArtifact,
    QuestionConversationArtifact,
    QuestionMemoryArtifact,
)
from app.services.context_budget import (
    ContextBudgetResolver,
    OperationContextPolicy,
    RenderedPromptGuard,
)
from app.services.context_language import classify_context_language
from app.services.context_runtime import (
    ContextRuntime,
    ContextRuntimeConfig,
    build_context_runtime,
)
from app.services.llm import LLMConfig
from app.services.provider_usage import (
    begin_provider_attempt,
    publish_prompt_measurement,
    publish_provider_response,
)


_SCHEMAS = {
    "question_conversation": QuestionConversationArtifact,
    "question_memory": QuestionMemoryArtifact,
    "evidence_compression": EvidenceCompressionArtifact,
    "prep_context": PrepContextArtifact,
}


QUESTION_MEMORY_COMPRESSION_POLICY = ContextCompressionPolicy(
    artifact_type="question_memory",
    policy_version="question-memory-v1",
    prompt_contract_version="question-memory-prompt-v1",
    output_schema_version="question-memory-v1",
    compressor_operation="context_compressor.question_memory",
    compressor_input_cap_tokens=16_000,
    target_output_tokens=2_000,
    max_output_units=16,
    max_supporting_excerpt_tokens=128,
)


def compressor_config_from_llm(config: LLMConfig) -> ContextCompressorConfig:
    return ContextCompressorConfig(
        provider="openai-compatible",
        model=config.model,
        base_url_identity=config.base_url,
        temperature=config.temperature,
        request_timeout_seconds=config.request_timeout_seconds,
        timeout_policy_version="request-timeout-v1",
        max_retries=config.max_retries,
        structured_output_mode="json-schema-v1",
        tokenizer_family=config.tokenizer_family,
    )


class OpenAIContextCompressor:
    """Dedicated, structured-output compression provider boundary."""

    def __init__(
        self,
        *,
        llm_config: LLMConfig | None = None,
        chat_model=None,
        context_runtime: ContextRuntime | None = None,
    ) -> None:
        resolved = llm_config or (
            LLMConfig(api_key="injected-chat-model")
            if chat_model is not None
            else LLMConfig.from_env()
        )
        self.llm_config = resolved
        self.config = compressor_config_from_llm(resolved)
        self.chat_model = chat_model or self._build_chat_model(resolved)
        self.context_runtime = context_runtime or build_context_runtime(
            ContextRuntimeConfig(
                model=resolved.model,
                base_url=resolved.base_url,
                context_window_tokens=resolved.context_window_tokens,
                protocol_reserve_tokens=resolved.protocol_reserve_tokens,
                structured_output_reserve_tokens=(
                    resolved.structured_output_reserve_tokens
                ),
                safety_margin_tokens=resolved.context_safety_margin_tokens,
                tokenizer_family=resolved.tokenizer_family,
            )
        )
        if self.context_runtime.model_profile.model != self.config.model:
            from app.services.model_capabilities import ContextConfigurationError

            raise ContextConfigurationError(
                "compressor model and ContextRuntime model must match"
            )
        self._budget_resolver = ContextBudgetResolver()
        self._prompt_guard = RenderedPromptGuard()

    def compress(
        self,
        *,
        policy: ContextCompressionPolicy,
        source_segments: Sequence[CompressionSourceSegment],
        expected_question_id_sha256: str | None = None,
        expected_evidence_content_sha256: str | None = None,
        expected_session_scope_sha256: str | None = None,
        expected_question_focus_sha256: str | None = None,
        expected_source_manifest_sha256: str | None = None,
    ) -> dict[str, Any]:
        schema = _SCHEMAS[policy.artifact_type]
        prompt = self._build_prompt(
            policy=policy,
            source_segments=source_segments,
            expected_question_id_sha256=expected_question_id_sha256,
            expected_evidence_content_sha256=(
                expected_evidence_content_sha256
            ),
            expected_session_scope_sha256=expected_session_scope_sha256,
            expected_question_focus_sha256=expected_question_focus_sha256,
            expected_source_manifest_sha256=expected_source_manifest_sha256,
        )
        operation_policy = OperationContextPolicy(
            operation=policy.compressor_operation,
            input_cap_tokens=policy.compressor_input_cap_tokens,
            max_output_tokens=policy.target_output_tokens,
            context_policy_version=policy.policy_version,
        )
        budget = self._budget_resolver.resolve(
            profile=self.context_runtime.model_profile,
            policy=operation_policy,
        )
        measurement = self._prompt_guard.validate(
            prompt=prompt,
            budget=budget,
            estimator=self.context_runtime.estimator_resolution,
        )
        publish_prompt_measurement(
            measurement,
            language_bucket=classify_context_language(prompt),
        )
        structured_model = self.chat_model.with_structured_output(
            schema,
            method="json_schema",
        )
        if hasattr(structured_model, "bind"):
            structured_model = structured_model.bind(
                max_tokens=policy.target_output_tokens
            )
        begin_provider_attempt()
        result = structured_model.invoke(prompt)
        publish_provider_response(result)
        validated = result if isinstance(result, schema) else schema.model_validate(result)
        return validated.model_dump(mode="json")

    @staticmethod
    def _build_prompt(
        *,
        policy: ContextCompressionPolicy,
        source_segments: Sequence[CompressionSourceSegment],
        expected_question_id_sha256: str | None,
        expected_evidence_content_sha256: str | None,
        expected_session_scope_sha256: str | None,
        expected_question_focus_sha256: str | None,
        expected_source_manifest_sha256: str | None,
    ) -> str:
        identity_fields = {
            "question_id_sha256": expected_question_id_sha256,
            "evidence_content_sha256": expected_evidence_content_sha256,
            "session_scope_sha256": expected_session_scope_sha256,
            "question_focus_sha256": expected_question_focus_sha256,
            "source_manifest_sha256": expected_source_manifest_sha256,
        }
        source_payload = [
            {
                "segment_index": source.segment_index,
                "segment_type": source.segment_type,
                "content_sha256": source.content_sha256,
                "content": source.content,
            }
            for source in source_segments
        ]
        return (
            "You are a deterministic context compressor.\n"
            "Return only the requested JSON schema.\n"
            "Every summary unit must cite source content_sha256 anchors.\n"
            "Supporting excerpts must be exact continuous source substrings.\n"
            "Question memory authority must be exactly non_authoritative and every claim must include at least one exact supporting excerpt.\n"
            "Do not introduce identifiers, numbers, facts, or conclusions that "
            "are absent from the cited source segments.\n"
            "Keep fixed field names and identity digests exactly unchanged.\n"
            f"artifact_type={policy.artifact_type}\n"
            f"output_schema_version={policy.output_schema_version}\n"
            f"target_output_tokens={policy.target_output_tokens}\n"
            "identity_fields="
            f"{json.dumps(identity_fields, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            "source_segments="
            f"{json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        )

    @staticmethod
    def _build_chat_model(config: LLMConfig):
        from langchain_openai import ChatOpenAI

        kwargs = {
            "api_key": config.api_key,
            "model": config.model,
            "temperature": config.temperature,
            "timeout": config.request_timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)
