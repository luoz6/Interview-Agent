from pathlib import Path


ADR = Path("docs/hosted-v2-productization-adr.md")


def adr_text() -> str:
    return ADR.read_text(encoding="utf-8")


def test_adr_is_review_ready_but_not_self_approved() -> None:
    text = adr_text()

    assert "ADR status:** `PROPOSED_FOR_EXTERNAL_DECISION`" in text
    assert "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED" in text
    assert "TASK_1_DECISION_PACKAGE=READY_FOR_EXTERNAL_REVIEW" in text
    assert "Silence, repository access" in text
    assert "not an approval record" in text


def test_adr_defines_go_no_go_and_revise_without_implicit_authority() -> None:
    text = adr_text()

    for outcome in ("`GO`", "`NO_GO`", "`REVISE`"):
        assert outcome in text
    assert "A `GO` authorizes only the next decision step" in text
    assert "does not authorize OIDC implementation" in text


def test_adr_pins_tenant_identity_and_recovery_boundaries() -> None:
    text = adr_text()

    for contract in (
        "isolated `deployment_id`",
        "stable random internal principal_id",
        "versioned HMAC alias",
        "must not automatically inherit old memory",
        "request-scoped Principal",
        "immutable opaque owner binding",
        "cross-deployment Principal lookup",
    ):
        assert contract in text


def test_adr_keeps_local_v1_unchanged_and_unmigrated() -> None:
    text = adr_text()

    assert "Local V1 remains the default product path" in text
    assert "must not silently convert" in text
    assert "not automatically migrated" in text
    assert "LOCAL_V1=UNCHANGED" in text


def test_adr_requires_operational_and_governance_ownership() -> None:
    text = adr_text()

    for owner in (
        "Product scope",
        "Change approval",
        "Operations, on-call",
        "Privacy rights",
        "Security threat response",
        "Fairness and Interview Quality",
        "Accessibility review",
        "Legal jurisdiction",
    ):
        assert owner in text
    assert "Names and external record locators are deliberately not stored in Git" in text


def test_adr_requires_candidate_rights_and_exit_operations() -> None:
    text = adr_text()

    for contract in (
        "Consent, correction, export, deletion",
        "Effective before the next context assembly and no later than 60 seconds",
        "Completed or explicitly failed within 24 hours",
        "tombstone replay proves residue zero",
        "must not reduce interview functionality, alter scoring",
    ):
        assert contract in text


def test_adr_blocks_implementation_until_both_decision_gates_pass() -> None:
    text = adr_text()

    assert "Tasks 4–34 remain blocked until Task 2 is approved" in text
    assert "TASKS_4_TO_34=BLOCKED_BY_PRODUCTIZATION_AND_DATA_USE_GATES" in text
    assert "separate Data-use Spec remains a mandatory gate" in text

