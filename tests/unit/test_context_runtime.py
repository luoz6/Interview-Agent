from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.graphs.durable_interview_graph import _recent_conversation_messages
from app.graphs.interview_graph import _build_followup_context
from app.graphs.interview_state import build_initial_state
from app.services.context_budget import ContextBudgetResolver
from app.services.context_runtime import (
    ContextRuntime,
    ContextRuntimeConfig,
    build_context_runtime,
)
from app.services.model_capabilities import (
    ContextConfigurationError,
    ModelRuntimeProfile,
)
from app.services.evaluator_ext import _budget_question_review_input
from app.services.llm import LLMConfig, OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.token_estimation import TokenEstimatorResolution


@dataclass
class RecordingEstimator:
    models: list[str] = field(default_factory=list)

    def estimate_text(self, text: str, *, model: str) -> int:
        self.models.append(model)
        return len(text)

    def estimate_messages(self, messages, *, model: str) -> int:
        self.models.append(model)
        return sum(len(item.get("content", "")) for item in messages)


def make_runtime(model: str = "gpt-4o"):
    estimator = RecordingEstimator()
    return ContextRuntime(
        model_profile=ModelRuntimeProfile(
            provider="test",
            model=model,
            context_window_tokens=128_000,
        ),
        estimator_resolution=TokenEstimatorResolution(
            estimator=estimator,
            estimator_path="exact_model",
            fallback_used=False,
        ),
        budget_resolver=ContextBudgetResolver(),
    ), estimator


def test_context_runtime_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = build_context_runtime(
        ContextRuntimeConfig(model="gpt-4o")
    )
    assert runtime.model_profile.model == "gpt-4o"


def test_custom_context_runtime_requires_explicit_window():
    with pytest.raises(ContextConfigurationError):
        build_context_runtime(
            ContextRuntimeConfig(
                model="private-model",
                base_url="https://provider.invalid",
            )
        )


def test_legacy_interview_uses_injected_context_model(monkeypatch):
    monkeypatch.setenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", "true")
    runtime, estimator = make_runtime("gpt-4o")
    plan = InterviewPlan(
        title="Plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Question",
                focus="Focus",
            )
        ],
    )
    state = build_initial_state(
        session_id="s1",
        plan=plan,
        job_description="JD",
        resume_text="Resume",
        job_tags=[],
    )
    state["messages"].append(
        {"role": "candidate", "content": "Answer", "question_id": "q1"}
    )
    _build_followup_context(state, context_runtime=runtime)
    assert estimator.models
    assert set(estimator.models) == {"gpt-4o"}


def test_durable_interview_uses_injected_context_model(monkeypatch):
    monkeypatch.setenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", "true")
    runtime, estimator = make_runtime("gpt-4o")
    state = {
        "current_index": 0,
        "plan_snapshot": {"questions": [{"id": "q1"}]},
        "messages": [
            {
                "role": "interviewer",
                "content": "Question",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "Answer",
                "question_id": "q1",
            },
        ],
    }

    selected = _recent_conversation_messages(state, runtime)

    assert [item["content"] for item in selected] == ["Question", "Answer"]
    assert estimator.models
    assert set(estimator.models) == {"gpt-4o"}


def test_review_budget_uses_injected_context_model():
    runtime, estimator = make_runtime("gpt-4o")
    chunk = SimpleNamespace(
        question_id="q1",
        messages=[
            {
                "role": "candidate",
                "content": "Answer",
                "question_id": "q1",
            }
        ],
    )

    messages, references = _budget_question_review_input(
        chunk,
        [],
        context_runtime=runtime,
    )

    assert messages == [{"role": "candidate", "content": "Answer"}]
    assert references == []
    assert estimator.models
    assert set(estimator.models) == {"gpt-4o"}


def test_llm_rejects_mismatched_context_runtime_model():
    runtime, _ = make_runtime("deepseek-chat")

    with pytest.raises(
        ContextConfigurationError,
        match="LLM model and ContextRuntime model must match",
    ):
        OpenAIInterviewLLM(
            config=LLMConfig(api_key="test-key", model="gpt-4o"),
            chat_model=object(),
            context_runtime=runtime,
        )
