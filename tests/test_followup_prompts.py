import re
from types import SimpleNamespace

import pytest

from app.services.decision_store import DecisionContract
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
    StructuredFollowupDecisionProvider,
    StructuredFollowupGenerationProvider,
    StructuredFollowupOutputError,
    generation_context_for_decision,
    render_followup_decision_prompt,
    render_followup_generation_prompt,
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
    assert FOLLOWUP_DECISION_PROMPT_VERSION == "followup-decision-v1"
    assert FOLLOWUP_GENERATION_PROMPT_VERSION == "followup-generation-v1"
    assert re.fullmatch(r"[0-9a-f]{64}", FOLLOWUP_DECISION_PROMPT_SHA256)
    assert re.fullmatch(r"[0-9a-f]{64}", FOLLOWUP_GENERATION_PROMPT_SHA256)
    assert FOLLOWUP_DECISION_PROMPT_SHA256 != FOLLOWUP_GENERATION_PROMPT_SHA256


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
                usage_metadata={"input_tokens": 30, "output_tokens": 6},
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

    assert model.bound == {"max_tokens": 120}
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
                    usage_metadata={"input_tokens": 40, "output_tokens": 8},
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
