import re

import pytest

from app.services.decision_store import DecisionContract
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
    StructuredFollowupDecisionProvider,
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

        def with_structured_output(self, schema, *, method):
            self.schema = schema
            self.method = method
            return self.structured

    chat = ChatModel()
    provider = StructuredFollowupDecisionProvider(chat, max_tokens=321)
    result = provider({"question": "q", "candidate_answers": ["a"]})

    assert chat.schema is DecisionContract
    assert chat.method == "json_schema"
    assert chat.structured.bound == {"max_tokens": 321}
    assert "CURRENT_QUESTION_INPUT_JSON" in chat.structured.prompt
    assert result.decision == decision()
    assert result.input_tokens is None
    assert result.output_tokens is None
