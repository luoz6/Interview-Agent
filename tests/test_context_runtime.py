from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import get_type_hints

import pytest

import app.services.context_runtime as context_runtime_module
from app.graphs.durable_interview_graph import _recent_conversation_messages
from app.graphs.interview_graph import _build_followup_context
from app.graphs.interview_state import build_initial_state
from app.services.context_budget import (
    ContextBudgetResolver,
    DynamicCompressionTargetPolicy,
)
from app.services.context_runtime import (
    ContextRuntime,
    ContextRuntimeConfig,
    build_context_runtime,
    get_context_runtime,
    reset_context_runtime_for_tests,
)
from app.services.context_source_identity import ContextSourceIdentityConfig
from app.services.model_capabilities import (
    ContextConfigurationError,
    ModelRuntimeProfile,
)
from app.services.evaluator_ext import _budget_question_review_input
from app.services.llm import LLMConfig, OpenAIInterviewLLM
from app.services.memory_config import load_effective_memory_config
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


def test_context_runtime_preserves_the_injected_source_identity_snapshot():
    snapshot = ContextSourceIdentityConfig(exact_deduplication_mode="shadow")

    runtime = build_context_runtime(
        ContextRuntimeConfig(
            model="gpt-4o",
            source_identity_config=snapshot,
        )
    )

    assert runtime.source_identity_config is snapshot


def test_context_runtime_config_exposes_optional_dynamic_compression_target_policy():
    annotations = get_type_hints(ContextRuntimeConfig)

    assert annotations["dynamic_compression_target_policy"] == (
        DynamicCompressionTargetPolicy | None
    )
    assert ContextRuntimeConfig().dynamic_compression_target_policy is None


def test_context_runtime_exposes_optional_dynamic_compression_target_policy():
    annotations = get_type_hints(ContextRuntime)

    assert annotations["dynamic_compression_target_policy"] == (
        DynamicCompressionTargetPolicy | None
    )


def test_context_runtime_config_from_env_uses_one_snapshot_for_dynamic_target_policy(
    monkeypatch,
):
    snapshot = load_effective_memory_config(
        {
            "MEMORY_SELECTION_DYNAMIC_TARGET_FLOOR_TOKENS": "384",
            "MEMORY_SELECTION_DYNAMIC_TARGET_SOURCE_RATIO_BASIS_POINTS": "3333",
            "MEMORY_SELECTION_DYNAMIC_TARGET_ALLOWED_TOKENS": (
                "384, 768, 1536, 2000"
            ),
        }
    )
    load_calls = []

    def load_snapshot_once():
        load_calls.append("load")
        if len(load_calls) > 1:
            raise AssertionError("context runtime must not reload memory config")
        return snapshot

    monkeypatch.setattr(
        "app.services.memory_config.load_effective_memory_config",
        load_snapshot_once,
    )

    config = ContextRuntimeConfig.from_env()

    assert load_calls == ["load"]
    assert config.dynamic_compression_target_policy == (
        DynamicCompressionTargetPolicy(
            floor_tokens=384,
            source_ratio_basis_points=3_333,
            allowed_target_tokens=(384, 768, 1_536, 2_000),
        )
    )


def test_build_context_runtime_preserves_injected_dynamic_compression_target_policy():
    policy = DynamicCompressionTargetPolicy(
        floor_tokens=384,
        source_ratio_basis_points=3_333,
        allowed_target_tokens=(384, 768, 1_536, 2_000),
    )
    config = ContextRuntimeConfig(
        model="gpt-4o",
        dynamic_compression_target_policy=policy,
    )

    runtime = build_context_runtime(config)

    assert runtime.dynamic_compression_target_policy is policy


def test_context_runtime_singleton_binds_first_resolved_config_and_rejects_conflicts(
    monkeypatch,
):
    config_a = ContextRuntimeConfig(
        model="gpt-4o",
        context_window_tokens=64_000,
    )
    config_b = replace(config_a)
    config_c = replace(config_a, safety_margin_tokens=2_048)
    assert config_b == config_a
    assert config_b is not config_a

    real_build = context_runtime_module.build_context_runtime
    build_calls = []
    env_calls = []

    def recording_build(config=None):
        build_calls.append(config)
        return real_build(config)

    def fail_env_reload(cls):
        del cls
        env_calls.append("reload")
        raise AssertionError("initialized singleton must not reload config")

    monkeypatch.setattr(
        context_runtime_module,
        "build_context_runtime",
        recording_build,
    )
    monkeypatch.setattr(
        ContextRuntimeConfig,
        "from_env",
        classmethod(fail_env_reload),
    )

    reset_context_runtime_for_tests()
    try:
        first = get_context_runtime(config_a)

        assert get_context_runtime(config_b) is first
        assert get_context_runtime(None) is first
        assert build_calls == [config_a]
        assert env_calls == []

        with pytest.raises(
            ContextConfigurationError,
            match="context runtime singleton configuration conflict",
        ):
            get_context_runtime(config_c)

        assert build_calls == [config_a]
        assert env_calls == []

        reset_context_runtime_for_tests()
        replacement = get_context_runtime(config_c)

        assert replacement is not first
        assert get_context_runtime(None) is replacement
        assert build_calls == [config_a, config_c]
        assert env_calls == []
    finally:
        reset_context_runtime_for_tests()


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
