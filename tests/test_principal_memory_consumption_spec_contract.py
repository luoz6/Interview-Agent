from pathlib import Path

import pytest

from app.services.memory_config import load_effective_memory_config


SPEC = Path("docs/principal-memory-consumption-spec.md")
RISK = Path("docs/principal-memory-consumption-risk-review.md")
STATUS_LINES = (
    "PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT",
    "IMPLEMENTATION=NOT_AUTHORIZED",
    "PRODUCTION_CANARY=NOT_AUTHORIZED",
)


def spec_text():
    return SPEC.read_text(encoding="utf-8")


def test_consumption_spec_declares_exact_non_authorization_status():
    text = spec_text()
    for line in STATUS_LINES:
        assert line in text
    assert "IMPLEMENTATION=AUTHORIZED" not in text
    assert "PRODUCTION_CANARY=AUTHORIZED" not in text


def test_consumption_preconditions_cover_identity_consent_and_user_controls():
    text = spec_text().casefold()
    for phrase in (
        "authenticated principal",
        "default off",
        "view",
        "confirm",
        "correct",
        "revoke",
        "delete",
        "export",
        "ignore memory for this interview",
        "disable memory now",
        "next context assembly",
        "no penalty",
        "privacy approval",
        "security approval",
        "fairness approval",
        "independent canary",
        "independent rollback",
        "independent observation",
    ):
        assert phrase in text


def test_allowed_facts_and_confirmed_skill_boundary_are_explicit():
    text = spec_text()
    for fact in (
        "interview_language",
        "accessibility_preference",
        "learning_goal",
        "target_role_family",
    ):
        assert f"`{fact}`" in text
    assert "`confirmed_skill` is excluded from C1" in text
    assert "must never affect scoring" in text


def test_prohibited_uses_cover_fairness_knowledge_and_current_evidence():
    text = spec_text().casefold()
    for phrase in (
        "historical score",
        "hiring recommendation",
        "personality",
        "integrity",
        "mental health",
        "physical health",
        "political",
        "religious",
        "ethnicity",
        "public knowledge",
        "cross-principal similarity",
        "implicit personalization",
        "current-session evidence",
        "negative label",
    ):
        assert phrase in text


def test_consumption_contract_is_bounded_visible_and_current_session_first():
    text = spec_text().casefold()
    for phrase in (
        "interview.followup.context_assembly",
        "candidate-visible",
        "non-authoritative historical preference",
        "maximum fact count: 3",
        "maximum token budget: 120",
        "source status",
        "authority",
        "exclude all conflicting values",
        "stale",
        "current-session evidence always wins",
        "fail open to deterministic interview",
        "fail closed for memory",
        "1%",
        "sticky session assignment",
        "automatic stop",
        "scoring and report isolation",
    ):
        assert phrase in text


def test_disable_and_deletion_slas_cover_inflight_and_backup_boundaries():
    text = " ".join(spec_text().casefold().split())
    for phrase in (
        "cannot retract an in-flight provider request",
        "within 60 seconds",
        "within 24 hours",
        "tombstone replay",
        "no new proposal",
        "no new selection",
        "must not reduce score",
        "must not remove features",
    ):
        assert phrase in text


def test_risk_review_has_decisions_mitigations_evidence_and_owners():
    text = RISK.read_text(encoding="utf-8").casefold()
    for phrase in (
        "severity",
        "mitigation",
        "required evidence",
        "approval owner",
        "identity collision",
        "consent dark pattern",
        "protected-class proxy",
        "historical anchoring",
        "prompt injection",
        "cross-principal",
        "backup resurrection",
        "disable race",
        "canary rollback",
        "residual risk",
    ):
        assert phrase in text
    assert "decision: do not implement" in text


def test_repository_still_has_no_consumption_implementation_or_route():
    forbidden_paths = [
        Path("app/services/principal_memory_consumption.py"),
        Path("app/ports/principal_memory_consumption.py"),
        Path("app/api/principal_memory_consumption.py"),
    ]
    assert all(not path.exists() for path in forbidden_paths)
    routes = Path("app/api/routes.py").read_text(encoding="utf-8").casefold()
    assert "/principal-memory/consume" not in routes
    assert "get_principal_memory_consumer" not in routes

    with pytest.raises(ValueError, match="consume is not supported"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
