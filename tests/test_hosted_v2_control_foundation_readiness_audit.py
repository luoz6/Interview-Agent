from pathlib import Path


AUDIT = Path("docs/hosted-v2-control-foundation-readiness-audit.md")


def audit_text() -> str:
    return AUDIT.read_text(encoding="utf-8")


def test_audit_is_complete_but_does_not_authorize_implementation() -> None:
    text = audit_text()

    assert "CONTROL_FOUNDATION_READINESS_AUDIT=COMPLETE" in text
    assert "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED" in text
    assert "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED" in text
    assert "CONTROL_FOUNDATION_IMPLEMENTATION=NOT_AUTHORIZED" in text
    assert "This audit does not execute Tasks 4–10" in text
    assert "The audit itself must never emit that output" in text


def test_audit_maps_every_control_foundation_task() -> None:
    text = audit_text()

    for number, title in (
        (4, "complete OIDC Authentication Runtime"),
        (5, "Stable Principal Mapping, Rotation, and Recovery"),
        (6, "request-scoped Principal and async owner binding"),
        (7, "Purpose-specific Consent Ledger v2"),
        (8, "Authenticated self-service API and Candidate Memory Center"),
        (9, "Runtime controls and complete lifecycle"),
        (10, "Control Foundation Acceptance"),
    ):
        assert f"### Task {number}: {title}" in text


def test_audit_records_the_confirmed_runtime_conflicts() -> None:
    text = audit_text()

    for conflict in (
        "No OIDC runtime",
        "Null runtime identity",
        "No subject alias mapping",
        "No immutable Session owner",
        "Async identity drift",
        "Aggregated Consent v1",
        "Missing `assist_c1a`",
        "Trusted-local API only",
        "No Memory Center",
        "Confirm is not atomic",
        "No exclusive active constraint",
        "Delete is incomplete",
        "Retention mismatch",
        "Write/Read coupling",
        "Null extractor",
    ):
        assert conflict in text


def test_audit_preserves_local_v1_and_additive_migration_rules() -> None:
    text = audit_text()

    assert "Local V1 Sessions remain null-owned" in text
    assert "do not backfill Local V1 Sessions into Principals" in text
    assert "migrate no grants from v1" in text
    assert "never rewrite `principal_memory_v1` history" in text
    assert "all Hosted flags disabled" in text


def test_audit_pins_identity_owner_and_consent_safety() -> None:
    text = audit_text()

    for contract in (
        "stable random internal IDs",
        "versioned deployment/issuer/subject aliases",
        "Raw issuer and subject do not enter these tables",
        "must never find a Principal by email, name, resume, device, IP, or similarity",
        "A database check requires both null or both non-null",
        "A singleton service factory is acceptable; a singleton current Principal is not",
        "V1 grants do not become V2 grants",
        "every purpose starts off",
    ):
        assert contract in text


def test_audit_pins_all_candidate_rights_and_lifecycle_slos() -> None:
    text = audit_text()

    for right in (
        "proposal confirm/reject",
        "active fact correct/revoke",
        "ignore for this Session",
        "disable now",
        "export request/status/download",
        "DELETE Principal Memory",
    ):
        assert right in text
    assert "within 60 seconds" in text
    assert "within 24 hours" in text
    assert "tombstone replay proves residue zero" in text


def test_audit_keeps_later_production_phases_closed() -> None:
    text = audit_text()

    for state in (
        "PRODUCTION_BUDGET_SHADOW=NOT_RUN",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_MEMORY_C1A_SPEC=DRAFT",
        "PRODUCTION_CANARY=NOT_AUTHORIZED",
    ):
        assert state in text
