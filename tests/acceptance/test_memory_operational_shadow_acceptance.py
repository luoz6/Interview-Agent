from __future__ import annotations

import base64
from copy import deepcopy
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
    OperationalRcEvidencePayload,
    OperationalRegressionEvidencePayload,
    OperationalSecurityEvidencePayload,
    OperationalStagingEvidencePayload,
    OperationalStatusEvidencePayload,
    ProposalReviewEvidencePayload,
    RestoreDrillEvidencePayload,
    ShadowEvidencePayload,
)
from contracts.policies import (
    OperationalRcEvidencePolicy,
    OperationalRegressionEvidencePolicy,
    OperationalSecurityEvidencePolicy,
    OperationalStagingEvidencePolicy,
    OperationalStatusEvidencePolicy,
    ProposalReviewEvidencePolicy,
    RestoreDrillEvidencePolicy,
    ShadowEvidencePolicy,
)
from scripts import memory_operational_shadow_acceptance as operational
from scripts import memory_production_shadow_approval_packet as approval_packet
from scripts.memory_operational_shadow_acceptance import (
    AcceptanceBlocked,
    SUCCESS_LINES,
    build_acceptance_evidence,
    evaluate_operational_shadow,
    format_blocked_output,
    validate_acceptance_artifact,
)


def _quality_payload() -> ProposalReviewEvidencePayload:
    return ProposalReviewEvidencePayload(
        schema_version="proposal-review-evidence-v1",
        review_case_count=300,
        revision_count=2,
        source_write_revision="abcdef1",
        source_write_receipt_sha256="a" * 64,
        review_revision="bcdefa2",
        approved_count=300,
        rejected_count=0,
        unresolved_count=0,
        label_counts={
            "correct": 300,
            "unsupported": 0,
            "over_generalized": 0,
            "wrong_taxonomy": 0,
            "stale_source": 0,
            "conflict": 0,
            "privacy_sensitive": 0,
            "not_useful": 0,
            "duplicate": 0,
            "review_unavailable": 0,
        },
        stale_source_accepted_count=0,
        raw_content_persisted=False,
        synthetic=True,
        review_digest="b" * 64,
    )


def test_operational_output_is_the_approval_packet_default_input():
    assert operational.DEFAULT_OUTPUT == approval_packet.DEFAULT_OPERATIONAL_EVIDENCE


def _shadow_payload(*, sample_count=300, violations=()):
    return ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=sample_count,
        synthetic=True,
        observation_window_seconds=1,
        metrics={"accepted_metric": 1.0},
        violations=list(violations),
    )


def _restore_payload():
    categories = {
        "business_sessions",
        "workflow_state",
        "messages",
        "reports",
        "question_memory",
        "artifact_owner_refs",
        "principal_memory_facts",
        "principal_memory_effects",
        "session_bound_consent_bindings",
    }
    return RestoreDrillEvidencePayload(
        schema_version="memory-shadow-restore-drill-evidence-v1",
        restore_cycles=3,
        tombstones_replayed=3,
        fault_boundaries_exercised=6,
        fault_reclaims_completed=6,
        restored_rows_by_category={name: 1 for name in categories},
        residue_by_category={name: 0 for name in categories},
        restored_private_data_residue=0,
        public_knowledge_file_count=25,
        public_knowledge_unchanged=True,
        provider_calls=0,
        production_observation="NOT_RUN",
        long_term_memory_consumption="BLOCKED",
        synthetic=True,
    )


def _rc_payload():
    return OperationalRcEvidencePayload(
        schema_version="operational-rc-evidence-v1",
        validated_rc_revision="bcdefa2",
        release_candidate_passed=True,
        clean_detached_worktree=True,
        shadow_modes_changed=False,
        full_python_passed=True,
        full_python_passed_count=1450,
        full_python_skipped=162,
        full_python_failed=0,
        postgres_passed=True,
        postgres_executed=43,
        postgres_failed=0,
        postgres_cleanup_verified=True,
        frontend_build_passed=True,
        frontend_modules_transformed=4587,
        browser_passed=True,
        browser_scope="full",
        browser_passed_count=54,
        browser_skipped=22,
        browser_failed=0,
        durable_metrics_passed=True,
        durable_metrics_store_kind="postgres_aggregate",
        durable_metrics_data_complete=True,
        test_listener_residue=0,
        isolated_relation_residue=0,
        safe_defaults_passed=True,
        consume_rejected=True,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _regression_payload():
    return OperationalRegressionEvidencePayload(
        schema_version="operational-regression-evidence-v1",
        validated_revision="bcdefa2",
        clean_detached_worktree=True,
        real_provider_calls=0,
        full_python_passed=True,
        full_python_passed_count=1500,
        full_python_skipped=160,
        full_python_failed=0,
        postgres_passed=True,
        postgres_executed=45,
        postgres_failed=0,
        frontend_build_passed=True,
        frontend_modules_transformed=4587,
        browser_passed=True,
        browser_scope="full",
        browser_passed_count=54,
        browser_skipped=22,
        browser_failed=0,
        compileall_passed=True,
        diff_check_passed=True,
        test_listener_residue=0,
        isolated_relation_residue=0,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _staging_payload():
    return OperationalStagingEvidencePayload(
        schema_version="operational-staging-evidence-v1",
        mode="EXECUTE",
        passed=True,
        gate_codes=[],
        validated_rc_revision="bcdefa2",
        environment_category="isolated_staging",
        observation_profile="B",
        configuration_changed=False,
        all_memory_shadows_disabled=True,
        real_provider_allowed=False,
        migration_scope="isolated",
        database_fingerprint_matches=True,
        prefix_valid=True,
        migration_validated=True,
        durable_metrics_validated=True,
        rollback_verified=True,
        cleanup_residue=0,
        live_validation_executed=True,
        worker_leasing_started=False,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _status_payload():
    return OperationalStatusEvidencePayload(
        schema_version="operational-status-evidence-v1",
        automatic_stop_triggered=False,
        automatic_stop_gate_codes=[],
        expansion_allowed=True,
        hold_codes=[],
        budget_data_complete=True,
        budget_sample_sufficient=True,
        write_sample_sufficient=True,
        read_sample_sufficient=True,
        prompt_isolation_violation_count=0,
        configuration_changed=False,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _security_payload():
    return OperationalSecurityEvidencePayload(
        schema_version="operational-security-evidence-v1",
        review_status="PASS",
        artifact_violations=0,
        artifacts_audited=9,
        hard_stop_count=0,
        knowledge_firewall_violations=0,
        protected_taxonomy_hits=0,
        prompt_attack_unsafe_writes=0,
        provider_calls=0,
        public_knowledge_unchanged=True,
        configuration_changed=False,
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _write_input_bundle(
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
        tool_version="2.0.0",
        revision=revision,
        scope=scope,
    )
    AtomicEvidenceWriter().write(path, bundle)
    return bundle


def _write_operational_inputs(tmp_path, signer):
    review_payload = _quality_payload()
    rc_payload = _rc_payload()
    regression_payload = _regression_payload()
    staging_payload = _staging_payload()
    status_payload = _status_payload()
    security_payload = _security_payload()
    definitions = {
        "rc": (
            "operational-rc-evidence",
            rc_payload,
            OperationalRcEvidencePolicy().evaluate(rc_payload),
            "memory.operational-rc.controlled",
        ),
        "regression": (
            "operational-regression-evidence",
            regression_payload,
            OperationalRegressionEvidencePolicy().evaluate(regression_payload),
            "memory.operational-regression.controlled",
        ),
        "staging": (
            "operational-staging-evidence",
            staging_payload,
            OperationalStagingEvidencePolicy().evaluate(staging_payload),
            "memory.staging-preflight.controlled",
        ),
        "status": (
            "operational-status-evidence",
            status_payload,
            OperationalStatusEvidencePolicy().evaluate(status_payload),
            "memory.shadow-status.controlled",
        ),
        "security": (
            "operational-security-evidence",
            security_payload,
            OperationalSecurityEvidencePolicy().evaluate(security_payload),
            "memory.shadow-security.controlled",
        ),
        "proposal_review": (
            "proposal-review-evidence",
            review_payload,
            ProposalReviewEvidencePolicy().evaluate(review_payload),
            "memory.proposal-review.controlled",
        ),
        "budget": (
            "shadow-evidence",
            _shadow_payload(),
            None,
            "memory.budget-shadow.controlled",
        ),
        "write": (
            "shadow-evidence",
            _shadow_payload(),
            None,
            "memory.write-shadow.controlled",
        ),
        "read": (
            "shadow-evidence",
            _shadow_payload(),
            None,
            "memory.read-shadow.controlled",
        ),
        "lifecycle": (
            "shadow-evidence",
            _shadow_payload(sample_count=5),
            None,
            "memory.lifecycle-shadow.controlled",
        ),
        "restore": (
            "restore-drill-evidence",
            _restore_payload(),
            None,
            "memory.shadow.restore-drill",
        ),
    }
    paths = {}
    for name, (payload_type, payload, policy_result, scope) in definitions.items():
        if policy_result is None and isinstance(payload, ShadowEvidencePayload):
            minimum_samples = 5 if name == "lifecycle" else 300
            policy_result = ShadowEvidencePolicy(
                minimum_samples=minimum_samples
            ).evaluate(payload, production_scope=False)
        elif policy_result is None:
            policy_result = RestoreDrillEvidencePolicy().evaluate(payload)
        path = tmp_path / f"{name}.json"
        _write_input_bundle(
            path=path,
            signer=signer,
            payload_type=payload_type,
            payload=payload,
            policy_result=policy_result,
            scope=scope,
        )
        paths[name] = path
    return paths


def accepted_bundle():
    return {
        "rc": _rc_payload(),
        "regression": _regression_payload(),
        "staging": _staging_payload(),
        "budget": _shadow_payload(),
        "write": _shadow_payload(),
        "quality": _quality_payload(),
        "read": _shadow_payload(),
        "lifecycle": _shadow_payload(sample_count=5),
        "restore": _restore_payload(),
        "status": _status_payload(),
        "security": _security_payload(),
        "repository": {
            "safe_defaults": True,
            "consume_rejected": True,
            "rc_revision_is_ancestor": True,
        },
    }


def test_all_operational_gates_produce_exact_success_lines_and_safe_evidence():
    bundle = accepted_bundle()

    lines = evaluate_operational_shadow(bundle)
    evidence = build_acceptance_evidence(bundle)

    assert lines == SUCCESS_LINES
    assert lines == (
        "MEMORY_SHADOW_RC=REPRODUCIBLE",
        "BUDGET_SHADOW_STAGING=PASS",
        "PRINCIPAL_WRITE_SHADOW_STAGING=PASS",
        "PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS",
        "CONSENT_DELETION_RESTORE_DRILL=PASS",
        "PRODUCTION_SHADOW_APPROVAL_REQUIRED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert evidence.operational_gates_passed is True
    assert evidence.validated_rc_revision == "bcdefa2"
    assert evidence.full_python_passed == 1500
    assert evidence.observation_profile == "B"
    assert evidence.private_data_residue == 0
    validate_acceptance_artifact(evidence)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (
            lambda value: value.__setitem__(
                "rc",
                value["rc"].model_copy(update={"release_candidate_passed": False}),
            ),
            "RC_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "regression",
                value["regression"].model_copy(
                    update={"full_python_passed": False}
                ),
            ),
            "REGRESSION_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "staging",
                value["staging"].model_copy(update={"rollback_verified": False}),
            ),
            "STAGING_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "budget",
                value["budget"].model_copy(
                    update={"violations": ["BUDGET_SHADOW_MANDATORY_CONTENT_LOSS"]}
                ),
            ),
            "BUDGET_SHADOW_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "write",
                value["write"].model_copy(
                    update={"violations": ["PRINCIPAL_WRITE_HARD_INVARIANT"]}
                ),
            ),
            "PRINCIPAL_WRITE_SHADOW_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "read",
                value["read"].model_copy(
                    update={"violations": ["PRINCIPAL_READ_ZERO_INJECTION_FAILED"]}
                ),
            ),
            "PRINCIPAL_READ_SHADOW_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "lifecycle",
                value["lifecycle"].model_copy(
                    update={"violations": ["CONSENT_LIFECYCLE_DRILL_FAILED"]}
                ),
            ),
            "CONSENT_LIFECYCLE_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "restore",
                value["restore"].model_copy(
                    update={"restored_private_data_residue": 1}
                ),
            ),
            "RESTORE_DRILL_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "status",
                value["status"].model_copy(
                    update={"automatic_stop_triggered": True}
                ),
            ),
            "STATUS_EVIDENCE_UNVERIFIED",
        ),
        (
            lambda value: value.__setitem__(
                "security",
                value["security"].model_copy(update={"review_status": "BLOCKED"}),
            ),
            "SECURITY_EVIDENCE_UNVERIFIED",
        ),
        (lambda value: value["repository"].update({"consume_rejected": False}), "CONSUME_NOT_REJECTED"),
    ],
)
def test_any_failed_gate_blocks_without_ready_output(mutator, code):
    bundle = accepted_bundle()
    mutator(bundle)

    with pytest.raises(AcceptanceBlocked) as raised:
        evaluate_operational_shadow(bundle)

    assert code in raised.value.codes
    output = format_blocked_output(raised.value.codes)
    assert output[0] == "MEMORY_OPERATIONAL_SHADOW=BLOCKED"
    assert f"GATE={code}" in output
    assert output[-2:] == (
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert not any("READY" in line or "=PASS" in line for line in output)


def test_legacy_proposal_quality_dictionary_is_not_trusted():
    bundle = accepted_bundle()
    bundle["quality"] = {
        "reviewed_count": 300,
        "privacy_sensitive_count": 0,
        "stale_source_accepted_count": 0,
        "quality_gate": "PASS",
    }

    with pytest.raises(AcceptanceBlocked) as raised:
        evaluate_operational_shadow(bundle)

    assert "PROPOSAL_REVIEW_EVIDENCE_UNVERIFIED" in raised.value.codes


def test_committed_default_enables_local_memory_and_legacy_consume_is_rejected():
    config = load_effective_memory_config({})
    assert config.budget.mode == "disabled"
    assert config.compression.mode == "disabled"
    assert config.long_term.mode == "local_consume"
    assert config.long_term.write_shadow_enabled is True
    assert config.long_term.read_shadow_enabled is True
    with pytest.raises(ValueError, match="consume is not supported"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})


def test_acceptance_artifact_rejects_private_keys_or_non_not_run_production():
    evidence = build_acceptance_evidence(accepted_bundle())
    unsafe = deepcopy(evidence.model_dump(mode="json"))
    unsafe["principal_id"] = "private"
    with pytest.raises(RuntimeError, match="private"):
        validate_acceptance_artifact(unsafe)

    unsafe = deepcopy(evidence.model_dump(mode="json"))
    unsafe["production_observation_not_run"] = False
    with pytest.raises(RuntimeError, match="production"):
        validate_acceptance_artifact(unsafe)


def test_cli_verifies_review_receipt_and_writes_signed_operational_bundle(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"o" * 32
    signer = HmacReceiptSigner(key_id="operational-test", secret=secret)
    paths = _write_operational_inputs(tmp_path, signer)
    output = tmp_path / "operational.json"
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "operational-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    monkeypatch.setattr(
        operational,
        "repository_snapshot",
        lambda _revision: {
            "safe_defaults": True,
            "consume_rejected": True,
            "rc_revision_is_ancestor": True,
        },
    )

    assert operational.main(
        [
            "--proposal-review-evidence",
            str(paths["proposal_review"]),
            "--proposal-review-revision",
            "bcdefa2",
            "--input-revision",
            "bcdefa2",
            "--rc-evidence",
            str(paths["rc"]),
            "--regression-evidence",
            str(paths["regression"]),
            "--staging-evidence",
            str(paths["staging"]),
            "--status-evidence",
            str(paths["status"]),
            "--security-evidence",
            str(paths["security"]),
            "--budget-evidence",
            str(paths["budget"]),
            "--write-evidence",
            str(paths["write"]),
            "--read-evidence",
            str(paths["read"]),
            "--lifecycle-evidence",
            str(paths["lifecycle"]),
            "--restore-evidence",
            str(paths["restore"]),
            "--output-revision",
            "cdefab3",
            "--evidence-output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="cdefab3",
        expected_scope="memory.operational-shadow.controlled",
    )
    assert verified.bundle.artifact.payload_type == "operational-shadow-evidence"
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert len(verified.bundle.artifact.envelope.input_manifest) == 11
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout


def test_cli_blocks_signed_inputs_from_the_wrong_revision(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"o" * 32
    signer = HmacReceiptSigner(key_id="operational-test", secret=secret)
    paths = _write_operational_inputs(tmp_path, signer)
    output = tmp_path / "operational.json"
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "operational-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert operational.main(
        [
            "--proposal-review-evidence",
            str(paths["proposal_review"]),
            "--proposal-review-revision",
            "bcdefa2",
            "--input-revision",
            "abcdef1",
            "--rc-evidence",
            str(paths["rc"]),
            "--regression-evidence",
            str(paths["regression"]),
            "--staging-evidence",
            str(paths["staging"]),
            "--status-evidence",
            str(paths["status"]),
            "--security-evidence",
            str(paths["security"]),
            "--budget-evidence",
            str(paths["budget"]),
            "--write-evidence",
            str(paths["write"]),
            "--read-evidence",
            str(paths["read"]),
            "--lifecycle-evidence",
            str(paths["lifecycle"]),
            "--restore-evidence",
            str(paths["restore"]),
            "--output-revision",
            "cdefab3",
            "--evidence-output",
            str(output),
        ]
    ) == 1

    assert not output.exists()
    stdout = capsys.readouterr().out
    assert "MEMORY_OPERATIONAL_SHADOW=BLOCKED" in stdout
    assert "GATE=OPERATIONAL_INPUT_EVIDENCE_UNVERIFIED" in stdout
    assert "=PASS" not in stdout
