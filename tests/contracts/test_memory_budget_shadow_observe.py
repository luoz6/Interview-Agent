"""Contracts for aggregate-only memory budget shadow observations."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json

import pytest

from app.runtime.config.memory import load_effective_memory_config
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    ShadowEvidencePayload,
)
from contracts.policies import (
    OperationalRcEvidencePolicy,
    OperationalStagingEvidencePolicy,
)
from scripts.memory_budget_shadow_observe import (
    SCENARIOS,
    build_budget_input_manifest,
    build_profile_b_cases,
    build_budget_shadow_payload,
    format_budget_input_blocked_output,
    main as budget_shadow_main,
    publish_budget_shadow_evidence,
    run_profile_b,
    shadow_environment,
    validate_observation_artifact,
    validate_single_shadow_axis,
    verify_budget_prerequisite_evidence,
)
from tests.operational_shadow_fixtures import rc_payload, staging_payload


class CompleteMetricStore:
    store_kind = "postgres_aggregate"

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def diagnostics(self):
        return {
            "store_kind": self.store_kind,
            "data_complete": True,
            "latest_bucket_at": "aggregate-only",
        }

    def aggregate(self, *, window_minutes):
        assert window_minutes == 1440
        return {
            "store_kind": self.store_kind,
            "data_complete": True,
            "items": [{"event_count": len(self.events)}],
        }


def _evidence_environment(secret: bytes) -> dict[str, str]:
    return {
        "EVIDENCE_REVISION": "bcdefa2",
        "EVIDENCE_HMAC_KEY_ID": "budget-input-test",
        "EVIDENCE_HMAC_SECRET_B64": base64.b64encode(secret).decode("ascii"),
    }


def _write_prerequisite_bundle(
    *,
    path,
    signer,
    payload_type,
    payload,
    policy_result,
    scope,
    revision="bcdefa2",
):
    bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type=payload_type,
        payload=payload,
        policy_result=policy_result,
        producer=f"tests.{payload_type}",
        tool_version="1.0.0",
        revision=revision,
        scope=scope,
    )
    AtomicEvidenceWriter().write(path, bundle)
    return bundle


def _write_budget_prerequisites(tmp_path, secret: bytes):
    signer = HmacReceiptSigner(key_id="budget-input-test", secret=secret)
    rc = rc_payload()
    staging = staging_payload()
    rc_path = tmp_path / "rc.json"
    staging_path = tmp_path / "staging.json"
    rc_bundle = _write_prerequisite_bundle(
        path=rc_path,
        signer=signer,
        payload_type="operational-rc-evidence",
        payload=rc,
        policy_result=OperationalRcEvidencePolicy().evaluate(rc),
        scope="memory.operational-rc.controlled",
    )
    staging_bundle = _write_prerequisite_bundle(
        path=staging_path,
        signer=signer,
        payload_type="operational-staging-evidence",
        payload=staging,
        policy_result=OperationalStagingEvidencePolicy().evaluate(staging),
        scope="memory.staging-preflight.controlled",
    )
    return rc_path, rc_bundle, staging_path, staging_bundle


def test_profile_b_matrix_has_300_balanced_sessions_and_required_scenarios():
    cases = build_profile_b_cases()

    assert len(cases) == 300
    assert {case.language_bucket for case in cases} == {"zh_hans", "en", "mixed"}
    assert {case.scenario for case in cases} == set(SCENARIOS)
    for language in ("zh_hans", "en", "mixed"):
        assert sum(case.language_bucket == language for case in cases) == 100


def test_single_axis_configuration_rejects_enforcement_or_memory_consumption():
    ready = load_effective_memory_config(shadow_environment())
    enforced = load_effective_memory_config(
        {
            **shadow_environment(),
            "MEMORY_BUDGET_MODE": "enforce",
            "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "true",
        }
    )

    assert validate_single_shadow_axis(ready) == []
    assert "BUDGET_SHADOW_NOT_ENABLED" in validate_single_shadow_axis(enforced)
    assert "BUDGET_ENFORCEMENT_MUST_REMAIN_DISABLED" in validate_single_shadow_axis(
        enforced
    )


def test_profile_b_observation_is_aggregate_only_and_never_changes_provider_input():
    store = CompleteMetricStore()

    record = run_profile_b(
        metric_store=store,
        validated_rc_revision="a982b1f",
        staging_preflight_revision="5280c9d",
    )
    validate_observation_artifact(record)

    assert record["session_count"] == 300
    assert record["language_sample_counts"] == {
        "en": 100,
        "mixed": 100,
        "zh_hans": 100,
    }
    assert record["scenario_counts"] == {name: 30 for name in sorted(SCENARIOS)}
    assert record["mandatory_current_content_losses"] == 0
    assert record["provider_calls"] == 0
    assert record["provider_input_change_count"] == 0
    assert record["data_complete"] is True
    assert record["followup_p95_latency_ms"] < 600
    assert record["baseline_p95_latency_ms"] == 500.0
    assert record["budget_mode_during_observation"] == "shadow"
    assert record["budget_mode_after_observation"] == "disabled"
    assert record["budget_enforcement"] == "disabled"
    assert record["principal_memory"] == "disabled"
    assert len(store.events) == 300

    record["cleanup_residue"] = 0
    record["rollback_verified"] = True
    payload = build_budget_shadow_payload(record, observation_hours=24)
    assert payload.sample_count == 300
    assert payload.synthetic is True
    assert payload.violations == []
    assert payload.observation_window_seconds == 86400
    assert payload.metrics["language_sample_count_en"] == 100.0
    assert sum(
        value
        for key, value in payload.metrics.items()
        if key.startswith("estimator_error_direction_")
    ) == 300.0
    assert payload.metrics["cleanup_residue"] == 0.0

    rendered = json.dumps(record, sort_keys=True).casefold()
    for forbidden in (
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "prompt",
        "answer",
        "postgresql://",
    ):
        assert forbidden not in rendered


def test_artifact_audit_rejects_subject_or_prompt_fields():
    for unsafe in (
        {"session_id": "private"},
        {"prompt": "private"},
        {"database_fingerprint": "private"},
    ):
        try:
            validate_observation_artifact(unsafe)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unsafe observation was accepted")


def test_budget_shadow_payload_derives_blockers_and_rejects_string_counts():
    store = CompleteMetricStore()
    record = run_profile_b(
        metric_store=store,
        validated_rc_revision="a982b1f",
        staging_preflight_revision="5280c9d",
    )
    record["cleanup_residue"] = 0
    record["rollback_verified"] = True
    record["privacy_audit_hits"] = 1
    payload = build_budget_shadow_payload(record, observation_hours=24)
    assert "BUDGET_SHADOW_PRIVACY_HIT" in payload.violations

    record["privacy_audit_hits"] = "0"
    try:
        build_budget_shadow_payload(record, observation_hours=24)
    except ValueError:
        pass
    else:
        raise AssertionError("string count was accepted")


def test_budget_prerequisites_verify_receipt_revision_scope_payload_and_policy(
    tmp_path,
):
    secret = b"b" * 32
    rc_path, rc_bundle, staging_path, staging_bundle = (
        _write_budget_prerequisites(tmp_path, secret)
    )

    verified_rc, verified_staging = verify_budget_prerequisite_evidence(
        rc_path=rc_path,
        rc_revision="bcdefa2",
        staging_path=staging_path,
        staging_revision="bcdefa2",
        environ=_evidence_environment(secret),
    )
    manifest = build_budget_input_manifest(
        rc_path=rc_path,
        rc_bundle=verified_rc.bundle,
        staging_path=staging_path,
        staging_bundle=verified_staging.bundle,
    )

    assert verified_rc.bundle == rc_bundle
    assert verified_staging.bundle == staging_bundle
    assert [item.path for item in manifest] == [
        "operational-rc-evidence",
        "operational-staging-evidence",
    ]
    rendered = json.dumps(
        [item.model_dump(mode="json") for item in manifest],
        sort_keys=True,
    )
    assert str(tmp_path) not in rendered


def test_budget_publisher_binds_both_prerequisites_without_absolute_paths(tmp_path):
    secret = b"e" * 32
    rc_path, rc_bundle, staging_path, staging_bundle = (
        _write_budget_prerequisites(tmp_path, secret)
    )
    output = tmp_path / "budget-output.json"
    payload = ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=300,
        synthetic=True,
        observation_window_seconds=86400,
        metrics={"execution_error_count": 0.0},
        violations=[],
    )

    bundle = publish_budget_shadow_evidence(
        payload=payload,
        output=output,
        environ=_evidence_environment(secret),
        rc_path=rc_path,
        rc_bundle=rc_bundle,
        staging_path=staging_path,
        staging_bundle=staging_bundle,
    )
    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=HmacReceiptSigner(
            key_id="budget-input-test",
            secret=secret,
        ),
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="bcdefa2",
        expected_scope="memory.budget-shadow.controlled",
    )

    assert verified.bundle == bundle
    assert [
        item.path for item in bundle.artifact.envelope.input_manifest
    ] == ["operational-rc-evidence", "operational-staging-evidence"]
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


def test_budget_prerequisites_fail_closed_for_revision_scope_and_receipt(tmp_path):
    secret = b"c" * 32
    rc_path, _, staging_path, _ = _write_budget_prerequisites(tmp_path, secret)
    environ = _evidence_environment(secret)

    with pytest.raises(ValueError):
        verify_budget_prerequisite_evidence(
            rc_path=rc_path,
            rc_revision="abcdef1",
            staging_path=staging_path,
            staging_revision="bcdefa2",
            environ=environ,
        )

    wrong_scope = tmp_path / "wrong-scope.json"
    rc = rc_payload()
    _write_prerequisite_bundle(
        path=wrong_scope,
        signer=HmacReceiptSigner(key_id="budget-input-test", secret=secret),
        payload_type="operational-rc-evidence",
        payload=rc,
        policy_result=OperationalRcEvidencePolicy().evaluate(rc),
        scope="memory.wrong.controlled",
    )
    with pytest.raises(ValueError):
        verify_budget_prerequisite_evidence(
            rc_path=wrong_scope,
            rc_revision="bcdefa2",
            staging_path=staging_path,
            staging_revision="bcdefa2",
            environ=environ,
        )

    tampered = json.loads(rc_path.read_text(encoding="utf-8"))
    tampered["receipt"]["signature"] = "AAAA"
    rc_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_budget_prerequisite_evidence(
            rc_path=rc_path,
            rc_revision="bcdefa2",
            staging_path=staging_path,
            staging_revision="bcdefa2",
            environ=environ,
        )


def test_budget_cli_rejects_unverified_inputs_before_database_access(
    tmp_path,
    monkeypatch,
    capsys,
):
    secret = b"d" * 32
    _, _, staging_path, _ = _write_budget_prerequisites(tmp_path, secret)
    invalid_rc = tmp_path / "invalid-rc.json"
    invalid_rc.write_text("{}", encoding="utf-8")
    output = tmp_path / "budget.json"
    for key, value in _evidence_environment(secret).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    code = budget_shadow_main(
        [
            "--execute",
            "--validated-rc-revision",
            "bcdefa2",
            "--staging-preflight-revision",
            "bcdefa2",
            "--rc-evidence",
            str(invalid_rc),
            "--staging-evidence",
            str(staging_path),
            "--scope-prefix",
            "test_memval_0123456789ab",
            "--output",
            str(output),
        ]
    )

    assert code == 1
    assert capsys.readouterr().out.splitlines() == list(
        format_budget_input_blocked_output()
    )
    assert not output.exists()
