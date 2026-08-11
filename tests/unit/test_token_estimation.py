"""Unit tests for deterministic token estimator resolution."""

from __future__ import annotations

import pytest

from app.services.token_estimation import (
    CompositeTokenEstimator,
    ConservativeUtf8TokenEstimator,
    ContextEstimatorUnavailable,
    TikTokenEstimator,
)


def test_unknown_model_uses_conservative_estimator_by_default():
    resolution = CompositeTokenEstimator().resolve(model="unknown-private-model")
    assert resolution.estimator_path == "conservative_utf8"
    assert resolution.fallback_used is True


def test_configured_family_is_explicit_fallback():
    resolution = CompositeTokenEstimator(
        configured_family="cl100k_base"
    ).resolve(model="unknown-private-model")
    assert resolution.estimator_path == "configured_family"
    assert resolution.fallback_used is True


def test_conservative_estimator_is_deterministic_for_chinese():
    estimator = ConservativeUtf8TokenEstimator()
    text = "候选人解释了缓存一致性。"
    assert estimator.estimate_text(text, model="unknown") == len(
        text.encode("utf-8")
    )


def test_tested_family_mapping_is_used_after_exact_lookup_failure(monkeypatch):
    original = TikTokenEstimator.estimate_text

    def fail_exact(self, text, *, model):
        if self.encoding_name is None:
            raise KeyError(model)
        return original(self, text, model=model)

    monkeypatch.setattr(TikTokenEstimator, "estimate_text", fail_exact)
    resolution = CompositeTokenEstimator(
        tested_family_mappings={"private-model": "cl100k_base"}
    ).resolve(model="private-model")
    assert resolution.estimator_path == "tested_family_mapping"


def test_all_estimators_unavailable_raises_stable_error(monkeypatch):
    monkeypatch.setattr(
        TikTokenEstimator,
        "estimate_text",
        lambda self, text, *, model: (_ for _ in ()).throw(KeyError(model)),
    )

    class FailingEstimator:
        def estimate_text(self, text, *, model):
            raise RuntimeError("unavailable")

        def estimate_messages(self, messages, *, model):
            raise RuntimeError("unavailable")

    with pytest.raises(ContextEstimatorUnavailable, match="no token estimator"):
        CompositeTokenEstimator(conservative=FailingEstimator()).resolve(
            model="private-model"
        )
