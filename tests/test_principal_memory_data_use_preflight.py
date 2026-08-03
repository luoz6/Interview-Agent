from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.hosted_v2_productization_preflight import (
    REQUIRED_ROLES as PRODUCT_ROLES,
    canonical_record_sha256,
)
from scripts.principal_memory_data_use_preflight import (
    PASS_LINES,
    PURPOSES,
    REQUIRED_APPROVAL_ROLES,
    REQUIRED_REVIEW_ROLES,
    DataUsePreflightBlocked,
    evaluate_data_use_preflight,
    format_blocked_output,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
ADR_DIGEST = "b" * 64
SPEC_DIGEST = "c" * 64
SCOPE_DIGEST = "d" * 64
REGIONS = ["approved-region"]


def product_record() -> dict[str, object]:
    decided_at = NOW - timedelta(hours=2)
    return {
        "schema_version": "hosted-v2-productization-decision-v1",
        "decision": "GO",
        "adr_sha256": ADR_DIGEST,
        "repository_revision": REVISION,
        "product_scope": "HOSTED_MULTI_USER_V2",
        "deployment_model": "DEPLOYMENT_SCOPED_MULTI_USER",
        "approved_regions": REGIONS,
        "oidc_provider_class": "external standards-compliant OIDC",
        "account_recovery_model": "NO_AUTOMATIC_MEMORY_INHERITANCE",
        "support_and_on_call_model": "externally owned",
        "local_v1_unchanged": True,
        "data_use_spec_still_required": True,
        "decision_time": decided_at.isoformat(),
        "expires_at": (NOW + timedelta(days=90)).isoformat(),
        "review_expiry_or_revalidation_trigger": "scope or provider change",
        "approvals": {
            role: {
                "decision": "APPROVED",
                "external_ref": f"external:{role}",
                "decided_at": (decided_at - timedelta(minutes=1)).isoformat(),
            }
            for role in PRODUCT_ROLES
        },
    }


def data_record() -> dict[str, object]:
    decided_at = NOW - timedelta(hours=1)
    return {
        "schema_version": "principal-memory-production-data-use-decision-v1",
        "decision": "APPROVED",
        "spec_sha256": SPEC_DIGEST,
        "repository_revision": REVISION,
        "deployment_scope_sha256": SCOPE_DIGEST,
        "approved_regions": REGIONS,
        "purposes": sorted(PURPOSES),
        "taxonomy_version": "principal-memory-taxonomy-v1",
        "candidate_notice_version": "candidate-notice-v1",
        "retention_schedule_version": "retention-v1",
        "controller_and_processor_roles": "externally approved roles",
        "lawful_basis_and_consent_requirements": "explicit purpose consent",
        "provider_and_subprocessors": ["approved-provider"],
        "provider_retention_and_training_setting": "no training; approved retention",
        "cross_border_transfer_mechanism": "approved transfer mechanism",
        "human_review_protocol_version": "proposal-review-v1",
        "deletion_export_slo": "24_HOURS",
        "disable_slo": "NEXT_ASSEMBLY_MAX_60_SECONDS",
        "decision_time": decided_at.isoformat(),
        "expires_at": (NOW + timedelta(days=90)).isoformat(),
        "revalidation_trigger": "purpose, provider, region, or retention change",
        "approvals": {
            role: {
                "decision": "APPROVED",
                "external_ref": f"external:{role}",
                "decided_at": (decided_at - timedelta(minutes=1)).isoformat(),
            }
            for role in REQUIRED_APPROVAL_ROLES
        },
        "reviews": {
            role: {
                "decision": "REVIEWED",
                "external_ref": f"external:{role}",
                "decided_at": (decided_at - timedelta(minutes=1)).isoformat(),
            }
            for role in REQUIRED_REVIEW_ROLES
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


def options(product=None, data=None, **overrides):
    product = product or product_record()
    data = data or data_record()
    value = {
        "productization_record": product,
        "expected_productization_record_sha256": canonical_record_sha256(product),
        "actual_productization_record_sha256": canonical_record_sha256(product),
        "actual_adr_sha256": ADR_DIGEST,
        "productization_record_is_external": True,
        "data_use_record": data,
        "expected_data_use_record_sha256": canonical_record_sha256(data),
        "actual_data_use_record_sha256": canonical_record_sha256(data),
        "actual_spec_sha256": SPEC_DIGEST,
        "expected_deployment_scope_sha256": SCOPE_DIGEST,
        "data_use_record_is_external": True,
        "current_revision": REVISION,
        "now": NOW,
        "repository": repository_state(),
    }
    value.update(overrides)
    return value


def test_valid_data_use_decision_passes_without_authorizing_production() -> None:
    assert evaluate_data_use_preflight(**options()) == PASS_LINES
    assert PASS_LINES[-4:] == (
        "CONFIGURATION_CHANGED=false",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRODUCTION_CANARY=NOT_AUTHORIZED",
    )


def test_data_use_roles_and_purposes_are_closed_sets() -> None:
    assert PURPOSES == {
        "proposal_write",
        "fact_storage",
        "read_shadow",
        "assist_c1a",
    }
    assert REQUIRED_APPROVAL_ROLES == (
        "product",
        "privacy",
        "security",
        "legal",
        "fairness",
        "operations",
    )
    assert REQUIRED_REVIEW_ROLES == ("accessibility", "interview_quality")


@pytest.mark.parametrize(
    ("mutator", "overrides", "code"),
    [
        (lambda value: value.update({"decision": "PENDING"}), {}, "DATA_USE_DECISION_NOT_APPROVED"),
        (lambda value: value.update({"purposes": ["fact_storage"]}), {}, "CONSENT_PURPOSES_INCOMPLETE"),
        (lambda value: value.update({"approved_regions": ["other"]}), {}, "DATA_USE_REGION_MISMATCH"),
        (lambda value: value.update({"provider_and_subprocessors": []}), {}, "PROVIDER_AND_SUBPROCESSORS_MISSING"),
        (lambda value: value.update({"deletion_export_slo": "7_DAYS"}), {}, "DELETION_EXPORT_SLO_MISMATCH"),
        (lambda value: value.update({"disable_slo": "BEST_EFFORT"}), {}, "DISABLE_SLO_MISMATCH"),
        (lambda value: value.update({"candidate_id": "private"}), {}, "DATA_USE_RECORD_FIELDS_INVALID"),
        (lambda value: value["approvals"].pop("privacy"), {}, "REQUIRED_DATA_USE_APPROVAL_NOT_GRANTED"),
        (lambda value: value["reviews"].pop("accessibility"), {}, "REQUIRED_CANDIDATE_COPY_REVIEW_NOT_COMPLETE"),
        (lambda value: None, {"actual_spec_sha256": "e" * 64}, "DATA_USE_SPEC_DIGEST_MISMATCH"),
        (lambda value: None, {"expected_deployment_scope_sha256": "f" * 64}, "DATA_USE_DEPLOYMENT_SCOPE_MISMATCH"),
        (lambda value: None, {"data_use_record_is_external": False}, "DATA_USE_RECORD_NOT_EXTERNAL"),
        (lambda value: None, {"actual_data_use_record_sha256": "0" * 64}, "DATA_USE_RECORD_HASH_MISMATCH"),
    ],
)
def test_invalid_data_use_record_is_blocked(mutator, overrides, code) -> None:
    data = data_record()
    mutator(data)
    with pytest.raises(DataUsePreflightBlocked) as raised:
        evaluate_data_use_preflight(**options(data=data, **overrides))
    assert code in raised.value.codes


def test_productization_cannot_be_skipped_or_replaced_by_data_approval() -> None:
    product = product_record()
    product["decision"] = "REVISE"
    with pytest.raises(DataUsePreflightBlocked) as raised:
        evaluate_data_use_preflight(**options(product=product))
    assert "PRODUCTIZATION_GATE_NOT_VERIFIED" in raised.value.codes


def test_data_use_decision_must_follow_productization_and_be_current() -> None:
    data = data_record()
    data["decision_time"] = (NOW - timedelta(hours=3)).isoformat()
    with pytest.raises(DataUsePreflightBlocked) as raised:
        evaluate_data_use_preflight(**options(data=data))
    assert "DATA_USE_PRECEDES_PRODUCTIZATION_GO" in raised.value.codes

    expired = data_record()
    expired["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(DataUsePreflightBlocked) as raised:
        evaluate_data_use_preflight(**options(data=expired))
    assert "DATA_USE_DECISION_NOT_CURRENT" in raised.value.codes


def test_blocked_output_is_content_free_and_keeps_gates_closed() -> None:
    output = format_blocked_output(("DATA_USE_DECISION_NOT_APPROVED",))

    assert output[0] == "PRINCIPAL_MEMORY_DATA_USE_PREFLIGHT=BLOCKED"
    assert "GATE=DATA_USE_DECISION_NOT_APPROVED" in output
    assert output[-1] == "PRODUCTION_CANARY=NOT_AUTHORIZED"
    assert not any("=PASS" in line for line in output)
    assert not any("external:" in line for line in output)


def test_how_to_prohibits_decision_material_in_git() -> None:
    text = Path(
        "docs/principal-memory-data-use-decision-preflight.md"
    ).read_text(encoding="utf-8")

    assert "Do not store either record, either digest" in text
    assert "Productization record cannot be omitted" in text
    assert "removes only the two decision gates" in text
    assert "Write Shadow, Read Shadow, C1-A" in text
