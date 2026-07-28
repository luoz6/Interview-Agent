from __future__ import annotations

from app.services.token_estimation import (
    CompositeTokenEstimator,
    ConservativeUtf8TokenEstimator,
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
