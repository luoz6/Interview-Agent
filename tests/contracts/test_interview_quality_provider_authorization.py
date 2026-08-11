from pathlib import Path

from app.services.interview_quality_provider_authorization import (
    ProviderRunRequest,
    load_provider_authorization,
    provider_authorization_sha256,
    validate_provider_run,
)


MANIFEST_PATH = Path("config/interview_quality_v1_provider_authorization.json")


def valid_request(**overrides):
    payload = {
        "task": "T27",
        "provider_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model_id": "deepseek-v4-pro",
        "data_categories": {"synthetic_candidate_answers"},
        "redaction_preflight_passed": True,
        "usage_metering_available": True,
        "evidence_persistence_available": True,
        "fallback_model": None,
    }
    payload.update(overrides)
    return ProviderRunRequest.model_validate(payload)


def test_unified_unlimited_authorization_loads_without_a_secret():
    manifest = load_provider_authorization(MANIFEST_PATH)

    assert set(manifest.allowed_tasks) == {"T27", "T36", "T57", "T65"}
    assert manifest.schema_version == "provider-authorization-manifest-v2"
    assert manifest.authorization_id == "interview-quality-v1-20260807-unlimited-02"
    assert (
        manifest.supersedes_authorization_id
        == "interview-quality-v1-20260805-unlimited-01"
    )
    assert manifest.provider.model_id == "deepseek-v4-pro"
    assert manifest.limits.total_budget == "unlimited"
    assert manifest.limits.total_outbound_requests == "unlimited"
    assert len(provider_authorization_sha256(MANIFEST_PATH)) == 64
    assert "api_key" not in MANIFEST_PATH.read_text(encoding="utf-8").lower()


def test_authorized_synthetic_run_has_no_stop_condition():
    manifest = load_provider_authorization(MANIFEST_PATH)

    assert validate_provider_run(manifest, valid_request()) == ()


def test_model_fallback_and_real_candidate_data_fail_closed():
    manifest = load_provider_authorization(MANIFEST_PATH)
    request = valid_request(
        fallback_model="other-model",
        data_categories={"real_candidate_identity_or_contact_data"},
    )

    assert validate_provider_run(manifest, request) == (
        "UNAPPROVED_MODEL_FALLBACK",
        "DATA_POLICY_VIOLATION",
    )


def test_metering_and_evidence_outage_stop_before_next_request():
    manifest = load_provider_authorization(MANIFEST_PATH)
    request = valid_request(
        usage_metering_available=False,
        evidence_persistence_available=False,
    )

    assert validate_provider_run(manifest, request) == (
        "USAGE_METERING_UNAVAILABLE",
        "EVIDENCE_PERSISTENCE_UNAVAILABLE",
    )
