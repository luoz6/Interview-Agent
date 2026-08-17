from types import SimpleNamespace

import pytest

from app.services.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetResolver,
    FOLLOWUP_CONTEXT_POLICY,
)
from app.services.llm import (
    LLMConfig,
    OpenAIInterviewLLM,
    _build_followup_prompt,
)


class CountingChatModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return SimpleNamespace(content="next question")


def make_llm(chat_model, *, context_window_tokens=700):
    return OpenAIInterviewLLM(
        config=LLMConfig(
            api_key="test",
            model="gpt-4o",
            context_window_tokens=context_window_tokens,
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
    context = [{"role": "candidate", "content": "x"}]
    calibration = make_llm(chat_model)
    prompt_tokens = calibration.token_estimator.estimator.estimate_text(
        _build_followup_prompt(context),
        model=calibration.model_profile.model,
    )
    llm = make_llm(
        chat_model,
        context_window_tokens=(
            FOLLOWUP_CONTEXT_POLICY.max_output_tokens + prompt_tokens - 1
        ),
    )
    budget = ContextBudgetResolver().resolve(
        profile=llm.model_profile,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )

    assert budget.available_input_tokens == prompt_tokens - 1

    with pytest.raises(ContextBudgetExceeded):
        llm.generate_followup(context)

    assert chat_model.calls == 0


def test_followup_enforcement_defaults_off_for_safe_rollout(monkeypatch):
    monkeypatch.delenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", raising=False)
    chat_model = CountingChatModel()
    llm = make_llm(chat_model)

    assert llm.generate_followup(
        [{"role": "candidate", "content": "x" * 1_000}]
    ) == "next question"
    assert chat_model.calls == 1


def test_followup_enforcement_reselects_before_provider_call(monkeypatch):
    monkeypatch.setenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", "true")
    chat_model = CountingChatModel()
    llm = make_llm(chat_model, context_window_tokens=1_600)

    result = llm.generate_followup(
        [
            {"role": "interviewer", "content": "old question " * 100},
            {"role": "candidate", "content": "old answer " * 100},
            {"role": "interviewer", "content": "current question"},
            {"role": "candidate", "content": "latest answer " * 100},
        ]
    )

    assert result == "next question"
    assert chat_model.calls == 1
