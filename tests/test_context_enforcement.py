from types import SimpleNamespace

import pytest

from app.services.context_budget import ContextBudgetExceeded
from app.services.llm import LLMConfig, OpenAIInterviewLLM


class CountingChatModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return SimpleNamespace(content="next question")


def make_llm(chat_model):
    return OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="test",
            model="gpt-4o",
            context_window_tokens=700,
            protocol_reserve_tokens=0,
            structured_output_reserve_tokens=0,
            context_safety_margin_tokens=0,
        ),
        chat_model=chat_model,
        trace_recorder=SimpleNamespace(record=lambda **kwargs: None),
    )


def test_followup_enforcement_rejects_before_provider_call(monkeypatch):
    monkeypatch.setenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", "true")
    chat_model = CountingChatModel()
    llm = make_llm(chat_model)

    with pytest.raises(ContextBudgetExceeded):
        llm.generate_followup(
            [{"role": "candidate", "content": "x" * 1_000}]
        )

    assert chat_model.calls == 0


def test_followup_enforcement_defaults_off_for_safe_rollout(monkeypatch):
    monkeypatch.delenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", raising=False)
    chat_model = CountingChatModel()
    llm = make_llm(chat_model)

    assert llm.generate_followup(
        [{"role": "candidate", "content": "x" * 1_000}]
    ) == "next question"
    assert chat_model.calls == 1
