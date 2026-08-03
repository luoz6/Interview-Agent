from pathlib import Path


SPEC = Path("docs/principal-memory-production-data-use-spec-v1.md")


def spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_spec_is_complete_review_material_but_not_approved() -> None:
    text = spec_text()

    assert "Spec status:** `DRAFT_FOR_EXTERNAL_REVIEW`" in text
    assert "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED" in text
    assert "Current entry-gate result:** `BLOCKED`" in text
    assert "TASK_2_REVIEW_MATERIAL=PREPARED_NOT_SUBMITTED" in text
    assert "REAL_CANDIDATE_PROCESSING=PROHIBITED" in text


def test_spec_defines_independent_default_off_consent_purposes() -> None:
    text = spec_text()

    for purpose in (
        "`proposal_write`",
        "`fact_storage`",
        "`read_shadow`",
        "`assist_c1a`",
    ):
        assert purpose in text
    assert "separately consented, versioned, default off" in text
    assert "never inherited from another purpose" in text
    assert "policy-version change requires new explicit Consent" in text


def test_spec_restricts_taxonomy_and_prohibits_free_text_and_inference() -> None:
    text = spec_text()

    assert "canonical JSON with versioned, allowlisted keys and enum values" in text
    assert "Free text and unknown fields are rejected" in text
    assert "direct declaration only; never inferred" in text
    assert "`learning_goal` | not approved for production" in text
    assert "`target_role_family` | not approved for production" in text
    assert "`confirmed_skill` | not approved for production" in text


def test_spec_pins_retention_and_candidate_rights_slos() -> None:
    text = spec_text()

    for retention in (
        "Unconfirmed proposal and opaque source binding | 7 days",
        "Candidate-confirmed fact | 180 days",
        "Export artifact | 24 hours",
        "Encrypted backups | 30 days",
        "oldest-backup lifetime plus 30 days",
    ):
        assert retention in text
    assert "before the next context assembly and no later than 60 seconds" in text
    assert "Export and online deletion must complete or return an explicit actionable failure within 24 hours" in text


def test_spec_requires_provider_privacy_and_failure_isolation() -> None:
    text = spec_text()

    for contract in (
        "no training or model improvement",
        "zero or the minimum explicitly approved Provider retention",
        "DPA and cross-border transfer mechanism",
        "strict structured output and unknown-field rejection",
        "Provider failure produces no proposal and does not change the interview path",
        "C1-A never sends Principal facts or accessibility preferences to the Provider",
    ):
        assert contract in text


def test_spec_constrains_human_review_and_routine_evidence() -> None:
    text = spec_text()

    assert "Human source review is prohibited until this specification" in text
    assert "minimum source excerpt needed" in text
    assert "no bulk browsing, local download, screenshots" in text
    assert "`privacy_sensitive=0` as a hard stop" in text
    assert "only sanitized low-cardinality aggregate results" in text
    assert "Hashing one of these values does not make it acceptable evidence" in text


def test_spec_keeps_accessibility_scoring_and_knowledge_isolated() -> None:
    text = spec_text()

    assert "`accessibility_preference` may originate only from a candidate's direct declaration" in text
    assert "never enters an LLM payload, evaluator, score, evidence, report" in text
    assert "Principal Memory never becomes an input to score, evidence, report" in text
    assert "public Knowledge, shared embeddings, or cross-Principal retrieval" in text


def test_spec_requires_complete_external_jurisdiction_contract() -> None:
    text = spec_text()

    for field in (
        "approved_data_region",
        "candidate_jurisdictions",
        "controller_and_processor_roles",
        "provider_and_subprocessors",
        "cross_border_transfer_mechanism",
        "retention_schedule_version",
        "expiry_or_revalidation_trigger",
    ):
        assert field in text
    assert "production preflight fails closed" in text


def test_spec_keeps_every_production_and_expansion_gate_closed() -> None:
    text = spec_text()

    for state in (
        "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED",
        "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_MEMORY_C1A_SPEC=DRAFT",
        "PRODUCTION_CANARY=NOT_AUTHORIZED",
    ):
        assert state in text
    assert "expansion, C1-B, or General Availability" in text
