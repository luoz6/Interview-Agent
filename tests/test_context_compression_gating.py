import pytest

from app.services.context_compression_gating import ContextCompressionGates
from app.services.config import (
    get_context_compression_evidence_enabled,
    get_context_compression_interview_enabled,
    get_context_compression_prep_enabled,
    get_context_compression_review_enabled,
    get_context_compression_shadow_enabled,
)


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
