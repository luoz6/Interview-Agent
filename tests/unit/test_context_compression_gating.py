from __future__ import annotations

import pytest

from app.services.context_compression_gating import ContextCompressionGates
from app.runtime.config.compatibility import (
    get_context_compression_evidence_enabled,
    get_context_compression_interview_enabled,
    get_context_compression_prep_enabled,
    get_context_compression_review_enabled,
    get_context_compression_shadow_enabled,
)
from app.runtime.config.memory import CompressionMemoryConfig


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            CompressionMemoryConfig(mode="disabled"),
            (False, False, False, False, False),
        ),
        (
            CompressionMemoryConfig(mode="shadow"),
            (True, False, False, False, False),
        ),
        (
            CompressionMemoryConfig(mode="consume"),
            (True, False, False, False, False),
        ),
        (
            CompressionMemoryConfig(
                mode="consume",
                prep=True,
                interview_question_memory=True,
                evidence=True,
                review=True,
            ),
            (True, True, True, True, True),
        ),
    ],
)
def test_from_config_maps_the_effective_compression_truth_table(config, expected):
    gates = ContextCompressionGates.from_config(config)

    assert (
        gates.shadow_enabled,
        gates.prep_enabled,
        gates.interview_enabled,
        gates.evidence_enabled,
        gates.review_enabled,
    ) == expected


@pytest.mark.parametrize("mode", ["shadow", "consume"])
def test_from_config_shadow_and_consume_create_without_workflow_consumption(mode):
    gates = ContextCompressionGates.from_config(
        CompressionMemoryConfig(mode=mode)
    )

    for workflow in ("prep", "interview", "review"):
        assert gates.creation_enabled(workflow=workflow) is True
        assert gates.consumption_enabled(
            workflow=workflow,
            artifact_type="question_conversation",
        ) is False


def test_from_config_consume_requires_both_workflow_and_evidence_flags():
    gates = ContextCompressionGates.from_config(
        CompressionMemoryConfig(
            mode="consume",
            interview_question_memory=True,
            evidence=True,
        )
    )

    assert gates.consumption_enabled(
        workflow="interview",
        artifact_type="question_conversation",
    ) is True
    assert gates.consumption_enabled(
        workflow="interview",
        artifact_type="evidence_compression",
    ) is True
    assert gates.consumption_enabled(
        workflow="review",
        artifact_type="evidence_compression",
    ) is False


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
@pytest.mark.parametrize("workflow", ["prep", "interview", "review"])
@pytest.mark.parametrize(
    "artifact_type",
    ["question_conversation", "evidence_compression"],
)
def test_from_config_non_consume_modes_never_consume_enabled_workflow_flags(
    mode,
    workflow,
    artifact_type,
):
    gates = ContextCompressionGates.from_config(
        CompressionMemoryConfig(
            mode=mode,
            prep=True,
            interview_question_memory=True,
            evidence=True,
            review=True,
        )
    )

    assert gates.consumption_enabled(
        workflow=workflow,
        artifact_type=artifact_type,
    ) is False


def test_all_context_compression_defaults_are_disabled(monkeypatch):
    for name in (
        "CONTEXT_COMPRESSION_SHADOW_ENABLED",
        "CONTEXT_COMPRESSION_PREP_ENABLED",
        "CONTEXT_COMPRESSION_INTERVIEW_ENABLED",
        "CONTEXT_COMPRESSION_EVIDENCE_ENABLED",
        "CONTEXT_COMPRESSION_REVIEW_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    assert get_context_compression_shadow_enabled() is False
    assert get_context_compression_prep_enabled() is False
    assert get_context_compression_interview_enabled() is False
    assert get_context_compression_evidence_enabled() is False
    assert get_context_compression_review_enabled() is False


def test_shadow_can_create_but_never_consume():
    gates = ContextCompressionGates(shadow_enabled=True)

    assert gates.creation_enabled(workflow="interview") is True
    assert (
        gates.consumption_enabled(
            workflow="interview",
            artifact_type="question_conversation",
        )
        is False
    )


def test_evidence_requires_workflow_and_independent_evidence_flag():
    workflow_only = ContextCompressionGates(interview_enabled=True)
    with_evidence = ContextCompressionGates(
        interview_enabled=True,
        evidence_enabled=True,
    )

    assert workflow_only.consumption_enabled(
        workflow="interview",
        artifact_type="question_conversation",
    )
    assert not workflow_only.consumption_enabled(
        workflow="interview",
        artifact_type="evidence_compression",
    )
    assert with_evidence.consumption_enabled(
        workflow="interview",
        artifact_type="evidence_compression",
    )


def test_invalid_boolean_fails_closed(monkeypatch):
    monkeypatch.setenv("CONTEXT_COMPRESSION_REVIEW_ENABLED", "yes")
    with pytest.raises(ValueError, match="must be true or false"):
        ContextCompressionGates.from_env()
