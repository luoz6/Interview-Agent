from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.hosted_v2_productization_preflight import (
    PASS_LINES,
    REQUIRED_ROLES,
    ProductizationPreflightBlocked,
    canonical_record_sha256,
    evaluate_productization_preflight,
    format_blocked_output,
    record_path_is_external,
    repository_snapshot,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
ADR_DIGEST = "b" * 64


def approved_record() -> dict[str, object]:
    decided_at = NOW - timedelta(hours=1)
    return {
        "schema_version": "hosted-v2-productization-decision-v1",
        "decision": "GO",
        "adr_sha256": ADR_DIGEST,
        "repository_revision": REVISION,
        "product_scope": "HOSTED_MULTI_USER_V2",
        "deployment_model": "DEPLOYMENT_SCOPED_MULTI_USER",
        "approved_regions": ["approved-region"],
        "oidc_provider_class": "standards-compliant external OIDC",
        "account_recovery_model": "NO_AUTOMATIC_MEMORY_INHERITANCE",
        "support_and_on_call_model": "externally-owned 24x7 window support",
        "local_v1_unchanged": True,
        "data_use_spec_still_required": True,
        "decision_time": decided_at.isoformat(),
        "expires_at": (NOW + timedelta(days=90)).isoformat(),
        "review_expiry_or_revalidation_trigger": "scope, provider, or region change",
        "approvals": {
            role: {
                "decision": "APPROVED",
                "external_ref": f"external:{role}",
                "decided_at": (decided_at - timedelta(minutes=1)).isoformat(),
            }
            for role in REQUIRED_ROLES
        },
    }


def repository_state() -> dict[str, object]:
    return {
        "execution_baseline_frozen": True,
        "plan_revision_revised": True,
        "safe_defaults": True,
        "production_unauthorized": True,
        "configuration_changed": False,
    }


def evaluate(record: dict[str, object] | None = None, **overrides):
    value = record or approved_record()
    options = {
        "record": value,
        "expected_record_sha256": canonical_record_sha256(value),
        "actual_record_sha256": canonical_record_sha256(value),
        "actual_adr_sha256": ADR_DIGEST,
        "current_revision": REVISION,
        "record_is_external": True,
        "now": NOW,
        "repository": repository_state(),
    }
    options.update(overrides)
    return evaluate_productization_preflight(**options)


def test_valid_external_go_unblocks_only_data_use_review() -> None:
    assert evaluate() == PASS_LINES
    assert PASS_LINES == (
        "HOSTED_PRODUCTIZATION_DECISION_PREFLIGHT=PASS",
        "EXTERNAL_PRODUCTIZATION_DECISION=VERIFIED_GO",
        "TASK_2_DATA_USE_REVIEW=UNBLOCKED",
        "TASKS_4_TO_34=BLOCKED_PENDING_DATA_USE_APPROVAL",
        "CONFIGURATION_CHANGED=false",
        "REAL_CANDIDATE_PROCESSING=PROHIBITED",
    )


def test_required_approval_roles_are_exact_and_independent() -> None:
    assert REQUIRED_ROLES == (
        "product",
        "change_owner",
        "operations",
        "privacy",
        "security",
        "fairness",
        "accessibility",
        "interview_quality",
        "legal",
    )


@pytest.mark.parametrize(
    ("mutator", "overrides", "code"),
    [
        (lambda value: value.update({"decision": "REVISE"}), {}, "PRODUCTIZATION_DECISION_NOT_GO"),
        (lambda value: value.update({"product_scope": "LOCAL_V1"}), {}, "PRODUCT_SCOPE_MISMATCH"),
        (lambda value: value.update({"deployment_model": "GLOBAL"}), {}, "DEPLOYMENT_MODEL_MISMATCH"),
        (lambda value: value.update({"local_v1_unchanged": False}), {}, "LOCAL_V1_BOUNDARY_NOT_ACCEPTED"),
        (lambda value: value.update({"data_use_spec_still_required": False}), {}, "DATA_USE_GATE_BYPASSED"),
        (lambda value: value.update({"account_recovery_model": "AUTO_MERGE"}), {}, "ACCOUNT_RECOVERY_MODEL_UNSAFE"),
        (lambda value: value.update({"approved_regions": []}), {}, "APPROVED_REGIONS_MISSING"),
        (lambda value: value.update({"oidc_provider_class": "TBD"}), {}, "OIDC_PROVIDER_CLASS_MISSING"),
        (lambda value: value.update({"candidate_id": "private"}), {}, "DECISION_RECORD_FIELDS_INVALID"),
        (lambda value: value["approvals"].pop("privacy"), {}, "REQUIRED_PRODUCTIZATION_APPROVAL_NOT_GRANTED"),
        (lambda value: value["approvals"]["privacy"].update({"note": "extra"}), {}, "REQUIRED_PRODUCTIZATION_APPROVAL_NOT_GRANTED"),
        (lambda value: value["approvals"]["security"].update({"decision": "PENDING"}), {}, "REQUIRED_PRODUCTIZATION_APPROVAL_NOT_GRANTED"),
        (lambda value: None, {"actual_adr_sha256": "c" * 64}, "ADR_DIGEST_MISMATCH"),
        (lambda value: None, {"current_revision": "d" * 40}, "REPOSITORY_REVISION_MISMATCH"),
        (lambda value: None, {"record_is_external": False}, "DECISION_RECORD_NOT_EXTERNAL"),
        (lambda value: None, {"actual_record_sha256": "e" * 64}, "DECISION_RECORD_HASH_MISMATCH"),
    ],
)
def test_invalid_or_unsafe_decision_is_blocked(mutator, overrides, code) -> None:
    record = approved_record()
    mutator(record)
    options = {
        "record": record,
        "expected_record_sha256": canonical_record_sha256(record),
        "actual_record_sha256": canonical_record_sha256(record),
        "actual_adr_sha256": ADR_DIGEST,
        "current_revision": REVISION,
        "record_is_external": True,
        "now": NOW,
        "repository": repository_state(),
    }
    options.update(overrides)

    with pytest.raises(ProductizationPreflightBlocked) as raised:
        evaluate_productization_preflight(**options)

    assert code in raised.value.codes


def test_expired_or_excessively_long_decision_is_blocked() -> None:
    expired = approved_record()
    expired["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(ProductizationPreflightBlocked) as raised:
        evaluate(expired)
    assert "DECISION_NOT_CURRENT" in raised.value.codes

    long_lived = approved_record()
    long_lived["expires_at"] = (NOW + timedelta(days=181)).isoformat()
    with pytest.raises(ProductizationPreflightBlocked) as raised:
        evaluate(long_lived)
    assert "DECISION_VALIDITY_TOO_LONG" in raised.value.codes


def test_repository_boundary_failure_blocks_a_valid_decision() -> None:
    for key, code in (
        ("execution_baseline_frozen", "EXECUTION_BASELINE_NOT_FROZEN"),
        ("plan_revision_revised", "PLAN_REVISION_MISMATCH"),
        ("safe_defaults", "SAFE_DEFAULTS_CHANGED"),
        ("production_unauthorized", "PRODUCTION_STATE_ALREADY_CHANGED"),
    ):
        state = repository_state()
        state[key] = False
        with pytest.raises(ProductizationPreflightBlocked) as raised:
            evaluate(repository=state)
        assert code in raised.value.codes


def test_blocked_output_is_content_free_and_never_claims_pass() -> None:
    output = format_blocked_output(("PRODUCTIZATION_DECISION_NOT_GO",))

    assert output[0] == "HOSTED_PRODUCTIZATION_DECISION_PREFLIGHT=BLOCKED"
    assert "GATE=PRODUCTIZATION_DECISION_NOT_GO" in output
    assert output[-1] == "REAL_CANDIDATE_PROCESSING=PROHIBITED"
    assert not any("=PASS" in line for line in output)
    assert not any("external:" in line for line in output)


def test_record_path_must_resolve_outside_the_repository(tmp_path: Path) -> None:
    assert record_path_is_external(tmp_path / "decision.json")
    assert not record_path_is_external(
        Path("docs/hosted-v2-productization-decision.json")
    )


def test_live_repository_snapshot_is_still_safe_and_unapproved() -> None:
    snapshot = repository_snapshot()

    assert snapshot == {
        "execution_baseline_frozen": True,
        "plan_revision_revised": True,
        "safe_defaults": True,
        "production_unauthorized": True,
        "configuration_changed": False,
    }


def test_how_to_keeps_external_records_out_of_git() -> None:
    text = Path(
        "docs/hosted-v2-productization-decision-preflight.md"
    ).read_text(encoding="utf-8")

    assert "Store the decision record and its expected digest" in text
    assert "Do not place the record, path, digest" in text
    assert "A `PASS` unblocks only Task 2 review" in text
    assert "does not authorize OIDC implementation" in text
