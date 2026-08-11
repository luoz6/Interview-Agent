"""Contracts for strict shared memory shadow evidence helpers."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from contracts.evidence import ShadowEvidencePayload
from scripts.memory_shadow_evidence_support import (
    publish_shadow_evidence,
    strict_finite_float,
    strict_nonnegative_int,
    zero_count_violations,
)


def _environment():
    return {
        "EVIDENCE_REVISION": "abcdef1",
        "EVIDENCE_HMAC_KEY_ID": "memory-shadow-v1",
        "EVIDENCE_HMAC_SECRET_B64": base64.b64encode(b"k" * 32).decode("ascii"),
    }


def test_strict_metric_helpers_reject_coercion_and_non_finite_values():
    assert strict_nonnegative_int({"count": 0}, "count") == 0
    assert strict_finite_float({"latency": 1.5}, "latency") == 1.5
    for value in ("0", False, 0.0, -1):
        with pytest.raises(ValueError):
            strict_nonnegative_int({"count": value}, "count")
    for value in ("1.5", 1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            strict_finite_float({"latency": value}, "latency")


def test_zero_count_violations_are_derived_from_strict_counts():
    assert zero_count_violations(
        {"privacy": 1, "mutation": 0},
        {"privacy": "SHADOW_PRIVACY_HIT", "mutation": "SHADOW_MUTATION"},
    ) == ["SHADOW_PRIVACY_HIT"]


def test_publish_shadow_evidence_signs_writes_and_reverifies(tmp_path):
    payload = ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=300,
        synthetic=True,
        observation_window_seconds=60,
        metrics={"error_count": 0.0},
        violations=[],
    )
    output = tmp_path / "shadow.json"

    bundle = publish_shadow_evidence(
        payload=payload,
        output=output,
        producer="tests.memory-shadow",
        scope="memory.shadow.controlled",
        environ=_environment(),
        minimum_samples=300,
    )

    assert output.exists()
    assert bundle.receipt.signature is not None
    assert bundle.artifact.promotion_decision.value == "CONTINUE_OBSERVATION"
