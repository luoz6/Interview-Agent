import re
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.followup_prompts as followup_prompts

from app.services.context_budget import FOLLOWUP_CONTEXT_POLICY
from app.services.decision_store import DecisionContract
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
    StructuredFollowupDecisionProvider,
    StructuredFollowupGenerationProvider,
    StructuredFollowupOutputError,
    build_followup_decision_provider,
    build_followup_decision_provider_for_llm,
    generation_context_for_decision,
    generation_context_for_target,
    render_followup_decision_prompt,
    render_followup_generation_prompt,
    resolve_followup_decision_output_mode,
)
from app.services.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
)


def decision(**updates):
    payload = {
        "action": "follow_up",
        "answer_state": "partial",
        "gap_type": "failure_mode",
        "gap_summary": "The recovery path after a failed write is missing.",
        "reason_code": "missing_failure_mode",
        "decision_confidence": "high",
        "closed_gap_ids": [],
        "policy_version": "adaptive_v1",
    }
    payload.update(updates)
    return DecisionContract.model_validate(payload)


def test_decision_and_generation_prompts_have_independent_frozen_lineage():
    assert FOLLOWUP_DECISION_PROMPT_VERSION == "followup-decision-v2"
    assert FOLLOWUP_GENERATION_PROMPT_VERSION == "followup-generation-v2"
    assert re.fullmatch(r"[0-9a-f]{64}", FOLLOWUP_DECISION_PROMPT_SHA256)
    assert re.fullmatch(r"[0-9a-f]{64}", FOLLOWUP_GENERATION_PROMPT_SHA256)
    assert FOLLOWUP_DECISION_PROMPT_SHA256 != FOLLOWUP_GENERATION_PROMPT_SHA256
    assert FOLLOWUP_DECISION_PROMPT_SHA256 == hashlib.sha256(
        followup_prompts._DECISION_PROMPT_TEMPLATE.encode("utf-8")
    ).hexdigest()
    assert FOLLOWUP_GENERATION_PROMPT_SHA256 == (
        "d9d19873f8cf793bb21e8c238d0ddf76704b7e8e7f950671f9f52d2a47e25f56"
    )
    assert FOLLOWUP_GENERATION_PROMPT_SHA256 == hashlib.sha256(
        followup_prompts._GENERATION_PROMPT_SPEC.encode("utf-8")
    ).hexdigest()


def test_followup_decision_v2_adr_binds_current_prompt_lineage():
    adr = Path(
        "docs/adr/followup-decision-provider-protocol-v2.md"
    ).read_text(encoding="utf-8")

    assert FOLLOWUP_DECISION_PROMPT_VERSION in adr
    assert FOLLOWUP_DECISION_PROMPT_SHA256 in adr
    assert "missing or empty model identity -> fail closed before request" in adr


def test_decision_prompt_forbids_question_score_reference_answer_and_reasoning():
    prompt = render_followup_decision_prompt(
        {
            "question": "How do you recover a failed write?",
            "candidate_answers": ["Use an idempotency key."],
            "policy": {"policy_version": "adaptive_v1", "max_followups": 2},
        }
    )

    assert f"prompt_version={FOLLOWUP_DECISION_PROMPT_VERSION}" in prompt
    assert "Do not generate a follow-up question" in prompt
    assert "numeric score" in prompt
    assert "hidden reasoning" in prompt
    assert "reference or ideal answer" in prompt
    assert "DECISION_JSON_SCHEMA=" in prompt
    assert '"additionalProperties":false' in prompt
    assert "Do not wrap the JSON in markdown code fences" in prompt


def test_generation_prompt_consumes_only_bounded_decision_target():
    context = generation_context_for_decision(
        [{"role": "candidate", "content": "I use retries."}],
        decision(),
    )
    prompt = render_followup_generation_prompt(context)

    assert context[0]["role"] == "system"
    assert "failure_mode" in context[0]["content"]
    assert "failed write" in context[0]["content"]
    assert "closed_gap_ids" not in context[0]["content"]
    assert "decision_confidence" not in context[0]["content"]
    assert f"prompt_version={FOLLOWUP_GENERATION_PROMPT_VERSION}" in prompt
    assert "Return only the question" in prompt

    with pytest.raises(ValueError, match="follow-up Decision"):
        generation_context_for_decision(
            [],
            decision(
                action="next_question",
                gap_type="none",
                gap_summary="",
                reason_code="answer_complete",
            ),
        )


@pytest.mark.parametrize(
    ("gap_type", "guidance"),
    [
        (
            "missing_detail",
            "Ask for one missing implementation detail.",
        ),
        (
            "tradeoff",
            "Ask for one tradeoff and why that choice was made.",
        ),
        (
            "failure_mode",
            "Ask about one failure scenario and its recovery boundary.",
        ),
        (
            "evidence",
            "Ask for one verifiable fact, metric, outcome, or personal contribution.",
        ),
        (
            "clarification",
            "Clarify one ambiguity without opening a new topic.",
        ),
        (
            "technical_error",
            "Ask the candidate to correct one wrong assumption or conclusion.",
        ),
    ],
)
def test_generation_prompt_freezes_specific_guidance_for_each_open_gap(
    gap_type,
    guidance,
):
    context = generation_context_for_target(
        [{"role": "candidate", "content": "My latest answer."}],
        gap_type=gap_type,
        gap_summary="Pursue exactly this bounded gap.",
    )

    prompt = render_followup_generation_prompt(context)

    assert f'"gap_type":"{gap_type}"' in prompt
    assert f"Pursue only FOLLOWUP_DECISION_TARGET: {guidance}" in prompt
    assert all(
        other_guidance not in prompt
        for other_type, other_guidance in followup_prompts._GENERATION_GAP_GUIDANCE.items()
        if other_type not in {gap_type, "none"}
    )


def test_generation_prompt_none_contract_never_enters_generation():
    assert followup_prompts._GENERATION_GAP_GUIDANCE["none"] == (
        "Generate nothing; continue to the next main question."
    )
    with pytest.raises(TypeError):
        followup_prompts._GENERATION_GAP_GUIDANCE["none"] = "generate"  # type: ignore[index]

    with pytest.raises(ValueError, match="one bounded open gap"):
        generation_context_for_target(
            [],
            gap_type="none",
            gap_summary="No open gap.",
        )


def test_generation_prompt_preserves_legacy_unknown_gap_target_compatibility():
    context = generation_context_for_target(
        [],
        gap_type="trade_off",
        gap_summary="Legacy checkpoint still has one bounded gap.",
    )

    assert '"gap_type":"trade_off"' in context[0]["content"]
    assert (
        "Pursue only FOLLOWUP_DECISION_TARGET: "
        "Ask only about the bounded gap_summary."
        in render_followup_generation_prompt(context)
    )


def test_generation_prompt_forbids_bundled_or_multi_question_output():
    prompt = render_followup_generation_prompt(
        generation_context_for_target(
            [{"role": "candidate", "content": "I used retries."}],
            gap_type="failure_mode",
            gap_summary="Recovery after the failed write is missing.",
        )
    )

    assert "One atomic interrogative sentence only" in prompt
    assert "no bundled gaps/subquestions, lists" in prompt
    assert "multiple question marks" in prompt
    assert "independent asks joined by and/or" in prompt
    assert all(marker in prompt for marker in ("同时", "以及", "另外"))


def test_structured_decision_provider_uses_schema_and_bounded_output():
    class StructuredModel:
        def __init__(self):
            self.bound = None
            self.prompt = None

        def bind(self, **kwargs):
            self.bound = kwargs
            return self

        def invoke(self, prompt):
            self.prompt = prompt
            return decision()

    class ChatModel:
        def __init__(self):
            self.structured = StructuredModel()
            self.schema = None
            self.method = None

        def with_structured_output(self, schema, *, method, include_raw=False):
            self.schema = schema
            self.method = method
            self.include_raw = include_raw
            return self.structured

    chat = ChatModel()
    provider = StructuredFollowupDecisionProvider(chat, max_tokens=321)
    result = provider({"question": "q", "candidate_answers": ["a"]})

    assert chat.schema is DecisionContract
    assert chat.method == "json_schema"
    assert chat.include_raw is True
    assert chat.structured.bound == {"max_tokens": 321}
    assert "CURRENT_QUESTION_INPUT_JSON" in chat.structured.prompt
    assert result.decision == decision()
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_structured_provider_preserves_raw_usage_outside_decision_contract():
    class StructuredModel:
        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            return {
                "raw": SimpleNamespace(
                    usage_metadata={
                        "input_tokens": 42,
                        "output_tokens": 9,
                        "input_token_details": {"cache_read": 7},
                    },
                    response_metadata={"model_name": "deepseek-chat"},
                    id="response-1",
                ),
                "parsed": decision(),
                "parsing_error": None,
            }

    class ChatModel:
        def with_structured_output(self, *args, **kwargs):
            return StructuredModel()

    result = StructuredFollowupDecisionProvider(ChatModel())(
        {"question": "q", "candidate_answers": ["a"]}
    )

    assert result.input_tokens == 42
    assert result.output_tokens == 9
    assert result.cached_input_tokens == 7
    assert result.provider_model == "deepseek-chat"
    assert result.provider_response_id == "response-1"
    assert "input_tokens" not in result.decision.model_dump(mode="json")


def test_structured_generation_provider_preserves_usage_and_binds_budget():
    class Model:
        def __init__(self):
            self.bound = None

        def bind(self, **kwargs):
            self.bound = kwargs
            return self

        def invoke(self, prompt):
            self.prompt = prompt
            return SimpleNamespace(
                content="  What failed during recovery?  ",
                usage_metadata={
                    "input_tokens": 30,
                    "output_tokens": 6,
                    "input_token_details": {"cache_read": 0},
                },
                response_metadata={"model_name": "deepseek-chat"},
                id="generation-1",
            )

    model = Model()
    provider = StructuredFollowupGenerationProvider(model)
    result = provider(
        [
            {
                "role": "system",
                "content": "[FOLLOWUP_DECISION_TARGET] failure mode",
            },
            {"role": "candidate", "content": "I retry."},
        ]
    )

    assert model.bound == {
        "max_tokens": FOLLOWUP_CONTEXT_POLICY.max_output_tokens,
    }
    assert "FOLLOWUP_DECISION_TARGET" in model.prompt
    assert result.text == "What failed during recovery?"
    assert result.input_tokens == 30
    assert result.output_tokens == 6
    assert result.provider_model == "deepseek-chat"
    assert result.provider_response_id == "generation-1"


def test_structured_decision_parse_error_retains_metering_metadata():
    class StructuredModel:
        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            return {
                "raw": SimpleNamespace(
                    usage_metadata={
                        "input_tokens": 40,
                        "output_tokens": 8,
                        "input_token_details": {"cache_read": 0},
                    },
                    response_metadata={"model_name": "deepseek-chat"},
                    id="invalid-response",
                ),
                "parsed": None,
                "parsing_error": ValueError("invalid JSON"),
            }

    class ChatModel:
        def with_structured_output(self, *args, **kwargs):
            return StructuredModel()

    with pytest.raises(StructuredFollowupOutputError) as raised:
        StructuredFollowupDecisionProvider(ChatModel())(
            {"question": "q", "candidate_answers": ["a"]}
        )

    assert raised.value.input_tokens == 40
    assert raised.value.output_tokens == 8
    assert raised.value.provider_model == "deepseek-chat"
    assert raised.value.provider_response_id == "invalid-response"


def test_followup_decision_output_mode_uses_exact_model_mapping():
    assert resolve_followup_decision_output_mode("deepseek-v4-pro") == "raw_only"
    assert (
        resolve_followup_decision_output_mode("deepseek-v4-flash")
        == "structured_first"
    )
    assert resolve_followup_decision_output_mode("DeepSeek-V4-Pro") == "structured_first"
    assert resolve_followup_decision_output_mode("deepseek-v4-pro-preview") == "structured_first"
    assert resolve_followup_decision_output_mode("other-model") == "structured_first"


class RawDecisionChatModel:
    def __init__(self, content: str, *, model: str = "deepseek-v4-pro"):
        self.content = content
        self.model = model
        self.bound = None
        self.invoke_count = 0
        self.structured_count = 0

    def bind(self, **kwargs):
        self.bound = kwargs
        return self

    def with_structured_output(self, *args, **kwargs):
        self.structured_count += 1
        raise AssertionError("raw_only must not construct a structured request")

    def invoke(self, prompt):
        self.invoke_count += 1
        self.prompt = prompt
        return SimpleNamespace(
            content=self.content,
            usage_metadata={
                "input_tokens": 55,
                "output_tokens": 13,
                "input_token_details": {"cache_read": 8},
            },
            response_metadata={"model_name": self.model},
            id="raw-decision-1",
        )


def raw_decision_json(**updates) -> str:
    payload = decision().model_dump(mode="json")
    payload.update(updates)
    return json.dumps(payload)


def test_raw_only_decision_is_one_metered_request_with_exact_model():
    chat = RawDecisionChatModel(raw_decision_json())
    provider = build_followup_decision_provider(
        chat,
        model="deepseek-v4-pro",
        max_tokens=333,
    )
    reset_provider_context_metadata()

    result = provider({"question": "q", "candidate_answers": ["a"]})
    usage = consume_provider_context_metadata()

    assert provider.output_mode == "raw_only"
    assert chat.bound == {"max_tokens": 333}
    assert chat.invoke_count == 1
    assert chat.structured_count == 0
    assert result.decision == decision()
    assert result.input_tokens == 55
    assert result.output_tokens == 13
    assert result.cached_input_tokens == 8
    assert result.provider_model == "deepseek-v4-pro"
    assert result.provider_response_id == "raw-decision-1"
    assert usage["provider_attempt_count"] == 1
    assert usage["provider_metered_attempt_count"] == 1
    assert usage["provider_usage_available"] is True


@pytest.mark.parametrize(
    "content",
    [
        "",
        "```json\n{}\n```",
        "Here is the result: {}",
        "{} trailing",
        "{} {}",
        "[]",
        '"scalar"',
        '{"action":"follow_up","action":"next_question"}',
        '{"nested":{"key":1,"key":2}}',
        raw_decision_json(extra_field=True),
        raw_decision_json(action="next_question"),
    ],
)
def test_raw_only_decision_rejects_noncanonical_or_invalid_json_once(content):
    chat = RawDecisionChatModel(content)
    provider = build_followup_decision_provider(
        chat,
        model="deepseek-v4-pro",
    )
    reset_provider_context_metadata()

    with pytest.raises(StructuredFollowupOutputError) as raised:
        provider({"question": "q", "candidate_answers": ["a"]})
    usage = consume_provider_context_metadata()

    assert chat.invoke_count == 1
    assert chat.structured_count == 0
    assert raised.value.input_tokens == 55
    assert raised.value.output_tokens == 13
    assert raised.value.cached_input_tokens == 8
    assert raised.value.provider_model == "deepseek-v4-pro"
    assert str(raised.value) == (
        "Provider Decision raw response failed JSON/schema validation"
    )
    assert usage["provider_attempt_count"] == 1
    assert usage["provider_metered_attempt_count"] == 1
    assert usage["provider_usage_available"] is True


@pytest.mark.parametrize("content", [None, [], {}])
def test_raw_only_decision_rejects_non_text_content_after_metering(content):
    chat = RawDecisionChatModel(content)  # type: ignore[arg-type]
    provider = build_followup_decision_provider(
        chat,
        model="deepseek-v4-pro",
    )
    reset_provider_context_metadata()

    with pytest.raises(StructuredFollowupOutputError) as raised:
        provider({"question": "q", "candidate_answers": ["a"]})
    usage = consume_provider_context_metadata()

    assert chat.invoke_count == 1
    assert raised.value.input_tokens == 55
    assert raised.value.output_tokens == 13
    assert str(raised.value) == (
        "Provider Decision raw response failed JSON/schema validation"
    )
    assert usage["provider_metered_attempt_count"] == 1


def test_structured_schema_validation_failure_retains_raw_usage():
    class StructuredModel:
        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            return {
                "raw": SimpleNamespace(
                    usage_metadata={
                        "input_tokens": 41,
                        "output_tokens": 7,
                        "input_token_details": {"cache_read": 2},
                    },
                    response_metadata={"model_name": "other-model"},
                    id="structured-schema-invalid",
                ),
                "parsed": {"action": "not-an-action"},
                "parsing_error": None,
            }

    class ChatModel:
        def with_structured_output(self, *args, **kwargs):
            return StructuredModel()

    with pytest.raises(StructuredFollowupOutputError) as raised:
        build_followup_decision_provider(
            ChatModel(),
            model="other-model",
        )({"question": "q", "candidate_answers": ["a"]})

    assert raised.value.input_tokens == 41
    assert raised.value.output_tokens == 7
    assert raised.value.cached_input_tokens == 2
    assert raised.value.provider_model == "other-model"


def test_raw_only_decision_model_mismatch_fails_after_metering_without_retry():
    chat = RawDecisionChatModel(raw_decision_json(), model="deepseek-v4-flash")
    provider = build_followup_decision_provider(
        chat,
        model="deepseek-v4-pro",
    )
    reset_provider_context_metadata()

    with pytest.raises(StructuredFollowupOutputError, match="model") as raised:
        provider({"question": "q", "candidate_answers": ["a"]})
    usage = consume_provider_context_metadata()

    assert chat.invoke_count == 1
    assert raised.value.provider_model == "deepseek-v4-flash"
    assert usage["provider_attempt_count"] == 1
    assert usage["provider_metered_attempt_count"] == 1


def test_invalid_followup_decision_output_mode_is_rejected_before_request():
    chat = RawDecisionChatModel(raw_decision_json())

    with pytest.raises(ValueError, match="unsupported followup Decision output_mode"):
        StructuredFollowupDecisionProvider(
            chat,
            output_mode="invalid",  # type: ignore[arg-type]
        )

    assert chat.invoke_count == 0
    assert chat.structured_count == 0


@pytest.mark.parametrize("model", [None, ""])
def test_followup_decision_factory_requires_exact_model_before_request(model):
    chat = RawDecisionChatModel(raw_decision_json())

    with pytest.raises(ValueError, match="requires an exact configured model"):
        build_followup_decision_provider(chat, model=model)  # type: ignore[arg-type]

    assert chat.invoke_count == 0
    assert chat.structured_count == 0


def test_llm_factory_uses_configured_model_and_fails_closed_without_it():
    raw_llm = SimpleNamespace(
        chat_model=RawDecisionChatModel(raw_decision_json()),
        config=SimpleNamespace(model="deepseek-v4-pro"),
    )
    structured_llm = SimpleNamespace(
        chat_model=object(),
        config=SimpleNamespace(model="other-model"),
    )

    raw_provider = build_followup_decision_provider_for_llm(raw_llm)
    structured_provider = build_followup_decision_provider_for_llm(structured_llm)

    assert raw_provider.output_mode == "raw_only"
    assert raw_provider.expected_model == "deepseek-v4-pro"
    assert structured_provider.output_mode == "structured_first"
    assert structured_provider.expected_model == "other-model"
    with pytest.raises(ValueError, match="requires an exact configured model"):
        build_followup_decision_provider_for_llm(
            SimpleNamespace(chat_model=object())
        )


def test_raw_only_decision_normalizes_response_metadata_usage_aliases():
    class AliasUsageModel(RawDecisionChatModel):
        def invoke(self, prompt):
            self.invoke_count += 1
            return SimpleNamespace(
                content=self.content,
                usage_metadata=None,
                response_metadata={
                    "model_name": "deepseek-v4-pro",
                    "token_usage": {
                        "prompt_tokens": 60,
                        "completion_tokens": 14,
                        "prompt_cache_hit_tokens": 9,
                    },
                },
                id="raw-decision-alias-usage",
            )

    chat = AliasUsageModel(raw_decision_json())
    result = build_followup_decision_provider(
        chat,
        model="deepseek-v4-pro",
    )({"question": "q", "candidate_answers": ["a"]})

    assert chat.invoke_count == 1
    assert result.input_tokens == 60
    assert result.output_tokens == 14
    assert result.cached_input_tokens == 9
